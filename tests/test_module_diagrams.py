"""module_diagrams.py in process: layers, configuration rules, diagrams, languages and the committed examples."""
import dataclasses
import json
import string

import pytest

import module_diagrams as md
from conftest import REPO

EXAMPLES = REPO / "examples"


def load(name):
    return json.loads((EXAMPLES / f"{name}.module-map.json").read_text(encoding="utf-8"))


def node(node_id, kind="class", **fields):
    return {"id": node_id, "kind": kind, "name": node_id.rsplit(".", 1)[-1], "file": "A.kt", "lines": [1, 1], **fields}


def edge(source, target, kind="depends_on"):
    return {"from": source, "to": target, "kind": kind, "provenance": ["ast"], "weight": 1}


def section_text(m, key):
    blocks, _ = md.SECTIONS[key](m, None)
    return "\n\n".join(blocks)


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


# ---------- the committed examples are what the script produces ----------

@pytest.mark.parametrize("name", ["feature-login", "core-network", "app"])
def test_examples_are_up_to_date(name):
    text, _ = md.build(md.Map(load(name)), None, list(md.SECTIONS))
    assert text == (EXAMPLES / f"{name}.md").read_text(encoding="utf-8"), \
        "regenerate with: python scripts/module_diagrams.py examples"


def test_example_index_is_up_to_date():
    names = ["app", "core-network", "feature-login"]
    maps = [md.Map(load(name)) for name in names]
    index = md.build_index(maps, [f"{name}.md" for name in names], "Acme")
    assert index == (EXAMPLES / "index.md").read_text(encoding="utf-8")


def test_example_index_page_is_up_to_date():
    names = ["app", "core-network", "feature-login"]
    maps = [md.Map(load(name)) for name in names]
    texts = [md.build(m, None, list(md.SECTIONS))[0] for m in maps]
    page = md.build_html(maps, texts, [f"{name}.md" for name in names], "Acme")
    assert page == (EXAMPLES / "index.html").read_text(encoding="utf-8")


# ---------- index ----------

def test_index_graph_links_each_module_to_its_document():
    maps = [md.Map(load(name)) for name in ["app", "feature-login"]]
    index = md.build_index(maps, ["app.md", "login/feature-login.md"], "Acme")
    assert 'click n0 href "app.md"' in index
    assert 'click n1 href "login/feature-login.md"' in index


def test_index_page_opens_modules_in_the_same_page():
    maps = [md.Map(load(name)) for name in ["app", "feature-login"]]
    page = md.build_html(maps, ["# app", "# login"], ["app.md", "feature-login.md"], "Acme", "en")
    data = json.loads(page.split('id="data">', 1)[1].split("</script>", 1)[0])
    assert 'click n1 href "#feature/login"' in data["home"]
    assert "[:feature:login](#feature/login)" in data["home"]
    login = data["modules"]["feature/login"]
    assert (login["path"], login["file"], login["markdown"]) == (":feature:login", "feature-login.md", "# login")
    assert login["summary"]["meta"][0] == "android-library"
    assert "<title>Acme: modules</title>" in page


def test_index_page_text_cannot_close_its_script():
    maps = [md.Map(load("app")), md.Map(load("core-network"))]
    page = md.build_html(maps, ["</script><script>alert(1)</script>", "x"], ["a.md", "b.md"], "Acme")
    assert page.count("</script>") == 2  # the data block and the viewer, nothing from the text


def test_module_page_shows_the_document_of_a_single_module():
    m = md.Map(load("feature-login"))
    text = md.build(m, None, list(md.SECTIONS))[0]
    page = md.build_module_html(m, text, "feature-login.md", "en")
    data = json.loads(page.split('id="data">', 1)[1].split("</script>", 1)[0])
    assert data["home"] == text
    assert data["file"] == "feature-login.md"
    assert data["modules"] == {}
    assert "<title>:feature:login</title>" in page
    assert page.count("</script>") == 2


def test_module_page_summary_counts_what_the_document_lists():
    summary = md.page_summary(md.Map(load("feature-login")))
    assert summary["meta"] == ["android-library", "com.acme.login"]
    assert summary["classes"]["layers"] == [["Presentation", 9], ["Domain", 3], ["Data", 5], ["DI", 1]]
    assert [(fact["section"], fact["count"]) for fact in summary["facts"]] == [
        ("Layer violations", 0), ("Edges to review", 2), ("External dependencies", 5)]


# ---------- layers ----------

@pytest.mark.parametrize("data, layer", [
    (node("com.acme.feature.ui.Screen"), "Presentation"),
    (node("com.acme.data.ui.Screen"), "Presentation"),  # the innermost known segment wins
    (node("com.acme.feature.Thing", role="usecase"), "Domain"),
    (node("com.acme.feature.UserRepository", kind="interface", role="repository"), "Domain"),
    (node("com.acme.feature.UserRepositoryImpl", role="repository"), "Data"),
    (node("com.acme.feature.Client", layer_hint="Data"), "Data"),
    (node("com.acme.feature.Thing"), "Other"),
])
def test_layer(data, layer):
    assert md.Map({"nodes": [data]}).layers[data["id"]] == layer


def test_other_takes_the_layer_of_its_neighbours():
    nodes = [node("com.x.Helper"), node("com.x.data.A"), node("com.x.data.B"), node("com.x.Lonely"),
             node("com.x.data.C")]
    edges = [edge("com.x.Helper", "com.x.data.A"), edge("com.x.data.B", "com.x.Helper"),
             edge("com.x.Lonely", "com.x.data.C")]
    layers = md.Map({"nodes": nodes, "edges": edges}).layers
    assert layers["com.x.Helper"] == "Data"
    assert layers["com.x.Lonely"] == "Other"  # a single neighbour is not enough evidence


# ---------- configuration ----------

def test_config_overrides_and_keeps_defaults():
    rules = md.rules_from_config({"lang": "en", "layers": {
        "by_segment": {"feature": "Presentation", "api": "Other"},
        "by_role": {"mapper": "Data"},
        "forbidden": [["Presentation", "Domain"]],
        "readable_limit": 60,
    }}, "cfg", "en")
    assert rules.by_segment["feature"] == "Presentation"
    assert rules.by_segment["api"] == "Other"
    assert rules.by_segment["domain"] == "Domain"
    assert rules.by_role["mapper"] == "Data"
    assert rules.by_role["viewmodel"] == "Presentation"
    assert rules.forbidden == frozenset({("Presentation", "Domain")})
    assert rules.readable_limit == 60


def test_empty_layers_table_gives_the_defaults():
    assert md.rules_from_config({"lang": "en"}, "cfg", "en") == md.DEFAULT_RULES


def test_example_config_holds_the_defaults():
    example = REPO / ".module-map.example.toml"
    config = md.toml_module().loads(example.read_text(encoding="utf-8"))
    assert config["lang"] == md.DEFAULT_LANG
    assert md.rules_from_config(config, example, "en") == md.DEFAULT_RULES


@pytest.mark.parametrize("config, message", [
    ({"colours": 1}, "unknown key `colours`"),
    ({"layers": {"strict": True}}, "unknown key `layers.strict`"),
    ({"layers": {"by_segment": {"ui": "View"}}}, "invalid value for `layers.by_segment.ui`: 'View'"),
    ({"layers": {"by_role": ["Data"]}}, "invalid value for `layers.by_role`"),
    ({"layers": {"forbidden": [["Presentation"]]}}, "invalid value for `layers.forbidden`"),
    ({"layers": {"forbidden": [["Presentation", "Model"]]}}, "invalid value for `layers.forbidden`"),
    ({"layers": {"readable_limit": 0}}, "invalid value for `layers.readable_limit`: 0"),
    ({"layers": {"readable_limit": True}}, "invalid value for `layers.readable_limit`: True"),
])
def test_invalid_config_stops_with_a_reason(config, message):
    with pytest.raises(SystemExit) as stopped:
        md.rules_from_config(config, "cfg", "en")
    assert message in str(stopped.value.code)


def test_config_is_found_up_to_the_project_root(tmp_path):
    project = tmp_path / "project"
    module = project / "feature" / "login"
    module.mkdir(parents=True)
    (project / "settings.gradle.kts").write_text("", encoding="utf-8")
    assert md.find_config(module) is None
    (tmp_path / md.CONFIG_FILE).write_text("", encoding="utf-8")  # above the project root: ignored
    assert md.find_config(module) is None
    (project / md.CONFIG_FILE).write_text("", encoding="utf-8")
    assert md.find_config(module) == project / md.CONFIG_FILE


# ---------- diagrams ----------

def test_layer_diagram_colors_each_layer():
    text = section_text(md.Map(load("feature-login")), "layers")
    assert "class n2,n3,n4,n5 layerPresentation" in text
    assert "class n0,n6 layerDomain" in text
    assert "class n9 layerDI" in text
    assert "class n10 external" in text


def test_flow_diagram_colors_each_layer():
    text = section_text(md.Map(load("feature-login")), "flows")
    assert "class n0 layerPresentation" in text
    assert "class n3,n4 layerData" in text


def test_large_module_is_drawn_by_package():
    rules = dataclasses.replace(md.DEFAULT_RULES, readable_limit=2)
    text = section_text(md.Map(load("feature-login"), rules, "es"), "layers")
    assert "10 nodos, más de 2" in text
    assert '["ui<br/>4 nodos"]' in text
    assert '["data<br/>3 nodos"]' in text
    assert '[":core:network"]' in text
    assert "-->|2|" in text
    assert "LoginViewModel" not in text


def test_short_package_names():
    assert md.short_names(["com.acme.ui", "com.acme.data.local"]) == {"com.acme.ui": "ui",
                                                                        "com.acme.data.local": "data.local"}
    assert md.short_names(["com.acme.ui"]) == {"com.acme.ui": "ui"}


def test_common_group():
    assert md.common_group([":feature:login", ":feature:home"]) == ":feature"
    assert md.common_group([":core", ":core:network"]) == ""
    assert md.common_group([":app"]) == ""


# ---------- violations ----------

def violation_map(forbidden):
    rules = dataclasses.replace(md.DEFAULT_RULES, forbidden=frozenset(forbidden))
    data = {"nodes": [node("com.x.domain.UseCase"), node("com.x.data.Api")],
            "edges": [edge("com.x.domain.UseCase", "com.x.data.Api")]}
    return md.Map(data, rules, "en")


def test_violation_is_reported_with_evidence():
    text = md.layer_violations(violation_map({("Domain", "Data")}))
    assert "| UseCase → Api | Domain → Data | depends_on | A.kt | - |" in text


def test_violation_rules_come_from_the_config():
    assert "No violations found" in md.layer_violations(violation_map({("Presentation", "Data")}))
    assert "There are no layer rules" in md.layer_violations(violation_map(set()))


# ---------- languages ----------

def test_messages_have_the_same_keys_and_placeholders_in_every_language():
    es, en = md.MESSAGES["es"], md.MESSAGES["en"]
    assert es.keys() == en.keys()
    for key in es:
        assert placeholders(es[key]) == placeholders(en[key]), key


def test_every_section_has_a_description():
    for lang in md.LANGS:
        for key in md.SECTIONS:
            assert md.MESSAGES[lang][f"section_{key}"]


def test_english_document():
    text, _ = md.build(md.Map(load("feature-login"), lang="en"), None, list(md.SECTIONS))
    for heading in ("# :feature:login: architecture", "## Layered architecture", "## Flows from the ViewModels",
                    "## Sequence: LoginViewModel.submit", "## Module dependencies", "## Dependency injection",
                    "## Classes", "## Layer violations", "## Entry points", "## External dependencies",
                    "## Edges to review"):
        assert heading in text
    assert "-.implemented by.->" in text
    assert "| LoginActivity | class | Presentation | activity | (no KDoc) |" in text
    for spanish in ("Arquitectura", "implementado", "sin KDoc", "Aristas", "librería"):
        assert spanish not in text
