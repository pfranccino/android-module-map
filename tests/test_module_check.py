import json
import os
import subprocess
import sys
import time

import pytest

import module_check
import module_diagrams

SCRIPT = os.path.join(os.path.dirname(module_check.__file__), "module_check.py")


def node(node_id, kind="class", **extra):
    return {"id": node_id, "kind": kind, "name": node_id.rsplit(".", 1)[-1],
            "file": "feature/orders/src/main/kotlin/" + node_id.replace(".", "/") + ".kt", **extra}


def write_module(root, nodes, externals=(), edges=(), codegraph="ok"):
    """A feature module with one source file and its map in docs/architecture, the map written last."""
    module = root / "feature" / "orders"
    source = module / "src" / "main" / "kotlin" / "Orders.kt"
    source.parent.mkdir(parents=True)
    source.write_text("package com.acme.orders\n", encoding="utf-8")
    (module / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (root / "settings.gradle.kts").write_text('include(":feature:orders")\n', encoding="utf-8")
    docs = module / "docs" / "architecture"
    docs.mkdir(parents=True)
    data = {"schema": "module-map/1", "module": {"path": ":feature:orders", "dir": "feature/orders"}, "nodes": list(nodes),
            "external_nodes": list(externals), "edges": list(edges),
            "sources": {"ast": {"files": 1}, "codegraph": {"status": codegraph}}}
    map_path = docs / "feature-orders.module-map.json"
    map_path.write_text(json.dumps(data), encoding="utf-8")
    later = time.time() + 5
    os.utime(map_path, (later, later))
    return map_path, source


def edge(source, target, kind="uses_type", **extra):
    return {"from": source, "to": target, "kind": kind, "provenance": ["ast"], **extra}


def run(path, *flags):
    result = subprocess.run([sys.executable, SCRIPT, str(path), "--json", *flags], capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip().startswith("{") else result


DOMAIN = "com.acme.orders.domain.CancelOrderUseCase"
DATA = "com.acme.orders.data.OrderRepositoryImpl"
UI = "com.acme.orders.presentation.OrderViewModel"


def test_clean_module_passes(tmp_path):
    write_module(tmp_path, [node(DOMAIN), node(UI)], edges=[edge(UI, DOMAIN)])
    code, result = run(tmp_path)
    assert code == 0
    assert result["status"] == "pass"
    assert result["maps"][0]["freshness"] == "fresh"


def test_layer_violation_inside_the_module(tmp_path):
    write_module(tmp_path, [node(DOMAIN), node(DATA)], edges=[edge(DOMAIN, DATA, details=[{"line": 7}])])
    code, result = run(tmp_path)
    assert code == 1
    violation = result["maps"][0]["violations"][0]
    assert violation["rule"] == "layer"
    assert violation["layers"] == ["Domain", "Data"]
    assert violation["where"].endswith(":7")


def test_domain_using_android_is_a_violation(tmp_path):
    android = {"id": "androidx.lifecycle.ViewModel", "name": "ViewModel", "origin": "library"}
    write_module(tmp_path, [node(DOMAIN)], [android], [edge(DOMAIN, android["id"], "extends")])
    code, result = run(tmp_path)
    assert code == 1
    violation = result["maps"][0]["violations"][0]
    assert violation["rule"] == "external"
    assert violation["prefix"] == "androidx."


def test_layer_violation_into_another_module(tmp_path):
    other = {"id": "com.acme.core.data.SharedDto", "name": "SharedDto", "origin": "project", "module": ":core:data"}
    write_module(tmp_path, [node(UI)], [other], [edge(UI, other["id"])])
    code, result = run(tmp_path)
    assert code == 1
    violation = result["maps"][0]["violations"][0]
    assert violation["scope"] == "project"
    assert violation["target_module"] == ":core:data"


def test_low_confidence_codegraph_edges_are_advisory(tmp_path):
    uncertain = {"from": DOMAIN, "to": DATA, "kind": "calls", "provenance": ["codegraph"],
                 "details": [{"confidence": 0.6}]}
    write_module(tmp_path, [node(DOMAIN), node(DATA)], edges=[uncertain])
    code, result = run(tmp_path)
    assert code == 0
    assert result["maps"][0]["violations"] == []
    assert result["maps"][0]["advisory"][0]["confidence"] == 0.6


def test_stale_map_is_incomplete_even_with_violations(tmp_path):
    map_path, source = write_module(tmp_path, [node(DOMAIN), node(DATA)], edges=[edge(DOMAIN, DATA)])
    later = map_path.stat().st_mtime + 60
    os.utime(source, (later, later))
    code, result = run(tmp_path)
    assert code == 2
    assert result["status"] == "incomplete"
    assert result["maps"][0]["freshness"] == "stale"
    assert result["violations"] == 1
    code, _ = run(tmp_path, "--skip-freshness")
    assert code == 1


def test_no_maps_is_incomplete(tmp_path):
    code, result = run(tmp_path)
    assert code == 2
    assert result["status"] == "incomplete"


def test_unclassified_classes_are_reported_and_can_fail(tmp_path):
    loose = "com.acme.orders.Helpers"
    write_module(tmp_path, [node(loose)])
    code, result = run(tmp_path)
    assert code == 0
    assert result["maps"][0]["unchecked"] == ["Helpers"]
    (tmp_path / ".module-map.toml").write_text("[check]\nfail_on_unchecked = true\n", encoding="utf-8")
    code, _ = run(tmp_path)
    assert code == 2


def test_check_config_replaces_the_external_table(tmp_path):
    gson = {"id": "com.google.gson.Gson", "name": "Gson", "origin": "library"}
    write_module(tmp_path, [node(DOMAIN)], [gson], [edge(DOMAIN, gson["id"])])
    assert run(tmp_path)[0] == 1
    (tmp_path / ".module-map.toml").write_text('[check]\nforbidden_external = { Domain = ["android."] }\n',
                                              encoding="utf-8")
    assert run(tmp_path)[0] == 0


def test_invalid_check_config_exits_2(tmp_path):
    write_module(tmp_path, [node(DOMAIN)])
    (tmp_path / ".module-map.toml").write_text("[check]\nfail_on_unchecked = \"yes\"\n", encoding="utf-8")
    code, result = run(tmp_path)
    assert code == 2
    (tmp_path / ".module-map.toml").write_text("[check]\ntypo = 1\n", encoding="utf-8")
    assert run(tmp_path)[0] == 2


def test_module_diagrams_accepts_the_check_table(tmp_path):
    config = {"check": {"fail_on_unchecked": True}}
    rules = module_diagrams.rules_from_config(config, tmp_path / ".module-map.toml", "en")
    assert rules.forbidden == module_diagrams.DEFAULT_RULES.forbidden


def test_text_output_in_spanish(tmp_path):
    write_module(tmp_path, [node(DOMAIN), node(DATA)], edges=[edge(DOMAIN, DATA)])
    result = subprocess.run([sys.executable, SCRIPT, str(tmp_path), "--lang", "es"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "violaciones" in result.stdout


@pytest.mark.parametrize("example", ["app", "core-network", "feature-login"])
def test_examples_run_without_errors(example):
    path = os.path.join(os.path.dirname(os.path.dirname(SCRIPT)), "examples", f"{example}.module-map.json")
    code, result = run(path, "--skip-freshness")
    assert code in (0, 1)
    assert result["schema_version"] == 1


def test_generate_checks_without_writing_docs(project):
    before = {p for p in project.rglob("*")}
    result = subprocess.run([sys.executable, SCRIPT, str(project / "feature" / "login"), "--generate", "--json"],
                            capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr
    summary = json.loads(result.stdout)
    assert summary["maps"] and all(m["freshness"] == "generated" and m["map"] is None for m in summary["maps"])
    created = {p for p in project.rglob("*")} - before
    assert not [p for p in created if "docs" in p.parts or p.name.endswith(".module-map.json")]


def test_generate_needs_a_folder(tmp_path):
    single = tmp_path / "x.module-map.json"
    single.write_text("{}", encoding="utf-8")
    result = subprocess.run([sys.executable, SCRIPT, str(single), "--generate"], capture_output=True, text=True)
    assert result.returncode == 2


def test_generate_never_runs_codegraph(tmp_path):
    command = module_check.generate_command(tmp_path, tmp_path / "out", None, "en")
    assert "--no-codegraph" in command


def test_forbidden_annotations_in_domain(tmp_path):
    entity = node(DOMAIN, annotations=["Entity(tableName = \"orders\")"])
    provider = node("com.acme.orders.domain.Wiring", functions=[{"name": "provide", "annotations": ["Provides"]}])
    write_module(tmp_path, [entity, provider])
    code, result = run(tmp_path)
    assert code == 1
    found = {(v["from"], v["annotation"]) for v in result["maps"][0]["violations"] if v["rule"] == "annotation"}
    assert found == {("CancelOrderUseCase", "Entity"), ("Wiring", "Provides")}


def write_map(folder, module_path, module_dir, nodes, externals=(), edges=(), namespace="com.acme.orders", **extra):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"schema": "module-map/1", "module": {"path": module_path, "dir": module_dir, "namespace": namespace}, "nodes": nodes,
            "external_nodes": list(externals), "edges": list(edges), "sources": {"codegraph": {"status": "skipped"}}, **extra}
    path = folder / (module_path.strip(":").replace(":", "-") + ".module-map.json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_library_origin_type_of_another_module_is_resolved_by_its_map(tmp_path):
    target = "com.acme.core.data.SharedDto"
    maps = tmp_path / "maps"
    write_map(maps, ":feature:orders", "feature/orders", [node(DOMAIN)],
              [{"id": target, "name": "SharedDto", "origin": "library"}], [edge(DOMAIN, target)])
    write_map(maps, ":core:data", "core/data", [node(target)], namespace="com.acme.core")
    code, result = run(maps, "--skip-freshness")
    assert code == 1
    violation = next(r for r in result["maps"] if r["module"] == ":feature:orders")["violations"][0]
    assert violation["scope"] == "project" and violation["target_module"] == ":core:data"


def test_unresolved_project_types_make_the_check_incomplete(tmp_path):
    maps = tmp_path / "maps"
    unknown = "com.acme.payments.data.PaymentDto"
    write_map(maps, ":feature:orders", "feature/orders", [node(DOMAIN)],
              [{"id": unknown, "name": "PaymentDto", "origin": "library"},
               {"id": "com.acme.orders.R", "name": "R", "origin": "library"}],
              [edge(DOMAIN, unknown), edge(DOMAIN, "com.acme.orders.R")])
    code, result = run(maps, "--skip-freshness")
    assert code == 2
    assert result["maps"][0]["unresolved"] == [unknown]
    (maps / ".module-map.toml").write_text("[check]\nfail_on_unresolved = false\n", encoding="utf-8")
    assert run(maps, "--skip-freshness")[0] == 0


def test_malformed_map_exits_2_with_json(tmp_path):
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "broken.module-map.json").write_text("{not json", encoding="utf-8")
    code, result = run(maps, "--skip-freshness")
    assert code == 2
    assert result["errors"]


def test_module_without_map_is_incomplete(tmp_path):
    write_module(tmp_path, [node(DOMAIN)])
    other = tmp_path / "feature" / "payments"
    (other / "src" / "main" / "kotlin").mkdir(parents=True)
    (other / "src" / "main" / "kotlin" / "Pay.kt").write_text("package x\n", encoding="utf-8")
    (other / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    code, result = run(tmp_path)
    assert code == 2
    assert result["missing_maps"] == ["feature/payments"]


def test_partial_parse_is_incomplete(tmp_path):
    maps = tmp_path / "maps"
    write_map(maps, ":feature:orders", "feature/orders", [node(DOMAIN)],
              warnings=["partial parse, a declaration may be missing: feature/orders/src/main/kotlin/A.kt"])
    code, result = run(maps, "--skip-freshness")
    assert code == 2
    assert result["maps"][0]["partial_parse"]


def test_central_docs_folder_uses_the_module_dir(tmp_path):
    map_path, source = write_module(tmp_path, [node(DOMAIN)])
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    central = tmp_path / "docs" / "architecture"
    central.mkdir(parents=True)
    moved = central / map_path.name
    map_path.replace(moved)
    later = time.time() + 5
    os.utime(moved, (later, later))
    code, result = run(moved)
    assert result["maps"][0]["freshness"] == "fresh"
    newer = later + 60
    os.utime(source, (newer, newer))
    code, result = run(moved)
    assert result["maps"][0]["freshness"] == "stale"


def test_low_confidence_edges_do_not_decide_layers(tmp_path):
    helper = "com.acme.orders.Helper"
    android = {"id": "android.content.Context", "name": "Context", "origin": "library"}
    uncertain = [{"from": helper, "to": DOMAIN, "kind": "calls", "provenance": ["codegraph"], "details": [{"confidence": 0.1}]},
                 {"from": helper, "to": "com.acme.orders.domain.Other", "kind": "calls", "provenance": ["codegraph"],
                  "details": [{"confidence": 0.1}]}]
    write_module(tmp_path, [node(helper), node(DOMAIN), node("com.acme.orders.domain.Other")], [android],
                 uncertain + [edge(helper, android["id"])])
    code, result = run(tmp_path)
    assert result["maps"][0]["violations"] == []
    assert "Helper" in result["maps"][0]["unchecked"]


def test_use_site_targets_are_read_like_module_map(tmp_path):
    prop = node(DOMAIN, properties=[{"name": "id", "annotations": ['field:SerializedName("id")']}])
    write_module(tmp_path, [prop])
    code, result = run(tmp_path)
    assert code == 1
    assert result["maps"][0]["violations"][0]["annotation"] == "SerializedName"
    assert result["limitations"]


def test_stale_supporting_map_makes_the_check_incomplete(tmp_path):
    target = "com.acme.core.data.SharedDto"
    maps = tmp_path / "maps"
    write_map(maps, ":feature:orders", "feature/orders", [node(DOMAIN)],
              [{"id": target, "name": "SharedDto", "origin": "library"}], [edge(DOMAIN, target)])
    write_map(maps, ":core:data", "core/data", [node(target)], namespace="com.acme.core",
              warnings=["partial parse, a declaration may be missing: core/data/src/main/kotlin/A.kt"])
    code, result = run(maps, "--skip-freshness")
    orders = next(r for r in result["maps"] if r["module"] == ":feature:orders")
    assert orders["supporting_maps"] and orders["supporting_maps"][0]["module"] == ":core:data"
    assert code == 2


def test_target_without_layer_in_a_supporting_map_is_reported(tmp_path):
    target = "com.acme.core.Shared"
    maps = tmp_path / "maps"
    write_map(maps, ":feature:orders", "feature/orders", [node(DOMAIN)],
              [{"id": target, "name": "Shared", "origin": "library"}], [edge(DOMAIN, target)])
    write_map(maps, ":core", "core", [node(target)], namespace="com.acme.core")
    code, result = run(maps, "--skip-freshness")
    assert next(r for r in result["maps"] if r["module"] == ":feature:orders")["unclassified_targets"] == [target]
    (maps / ".module-map.toml").write_text("[check]\nfail_on_unchecked = true\n", encoding="utf-8")
    assert run(maps, "--skip-freshness")[0] == 2


def test_test_only_modules_need_no_map(tmp_path):
    write_module(tmp_path, [node(DOMAIN)])
    tests_only = tmp_path / "feature" / "fixtures"
    (tests_only / "src" / "test" / "kotlin").mkdir(parents=True)
    (tests_only / "src" / "test" / "kotlin" / "OnlyTest.kt").write_text("package x\n", encoding="utf-8")
    (tests_only / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    code, result = run(tmp_path)
    assert result["missing_maps"] == []
    assert code == 0


@pytest.mark.parametrize("content", ["{}", '{"module": {"dir": "feature/orders"}}',
                                     '{"schema": "module-map/1", "module": {"path": ":x"}, "nodes": []}'])
def test_documents_that_are_not_maps_are_errors(tmp_path, content):
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "x.module-map.json").write_text(content, encoding="utf-8")
    code, result = run(maps, "--skip-freshness")
    assert code == 2
    assert result["errors"]


def test_filesystem_errors_exit_2_with_json(tmp_path, monkeypatch):
    write_module(tmp_path, [node(DOMAIN)])

    def broken(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(module_check, "newest_source", broken)
    monkeypatch.setattr(sys, "argv", ["module_check.py"])
    code = module_check.main([str(tmp_path), "--json"])
    assert code == 2


def test_flat_maps_in_the_project_root_still_check_coverage(tmp_path):
    write_module(tmp_path, [node(DOMAIN)])
    other = tmp_path / "feature" / "payments"
    (other / "src" / "main" / "kotlin").mkdir(parents=True)
    (other / "src" / "main" / "kotlin" / "Pay.kt").write_text("package x\n", encoding="utf-8")
    (other / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    source_map = tmp_path / "feature" / "orders" / "docs" / "architecture" / "feature-orders.module-map.json"
    flat = tmp_path / "feature-orders.module-map.json"
    flat.write_text(source_map.read_text(encoding="utf-8"), encoding="utf-8")
    later = source_map.stat().st_mtime
    os.utime(flat, (later, later))
    code, result = run(tmp_path)
    assert "feature/payments" in result["missing_maps"]
    assert code == 2
