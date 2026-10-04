"""Tests for module_map.py and module_diagrams.py.

Runs against:
  - Synthetic Kotlin fixtures in tests/fixtures/ (module_map.py, AST-only)
  - Pre-built example JSON maps in examples/ (module_diagrams.py)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
MAP_SCRIPT = SCRIPTS / "module_map.py"
DIAGRAM_SCRIPT = SCRIPTS / "module_diagrams.py"
FIXTURES = REPO / "tests" / "fixtures"
EXAMPLES = REPO / "examples"
PYTHON = sys.executable

passed = 0
failed = 0


def run(script, *args, expect_ok=True):
    result = subprocess.run(
        [PYTHON, str(script), *args],
        capture_output=True, text=True, timeout=120,
    )
    if expect_ok and result.returncode != 0:
        raise RuntimeError(f"{script.name} failed (rc={result.returncode}):\n{result.stderr}\n{result.stdout}")
    return result


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


# ─── module_map.py against synthetic fixtures ────────────────────────────

def test_module_map():
    print("\n=== module_map.py (AST-only, synthetic fixtures) ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "map.json"
        run(MAP_SCRIPT, str(FIXTURES / "feature" / "login"), "--no-codegraph", "-o", str(out))

        check("output file created", out.is_file())
        data = json.loads(out.read_text(encoding="utf-8"))

        check("schema is module-map/1", data.get("schema") == "module-map/1")
        check("has generated_at", "generated_at" in data)
        check("has legend", "legend" in data)

        # Project
        project = data.get("project", {})
        check("project name is TestProject", project.get("name") == "TestProject")
        check("project includes :feature:login", ":feature:login" in project.get("modules", []))

        # Module
        module = data.get("module", {})
        check("module path is :feature:login", module.get("path") == ":feature:login")
        check("module namespace", module.get("namespace") == "com.acme.login")
        check("module type is android-library", module.get("type") == "android-library")

        # Plugins
        plugins = module.get("plugins", [])
        check("hilt plugin detected", any("hilt" in p for p in plugins))

        # Dependencies
        deps = module.get("dependencies", {})
        module_deps = [d["path"] for d in deps.get("modules", [])]
        check("depends on :core:network", ":core:network" in module_deps)
        check("depends on :core:model", ":core:model" in module_deps)
        lib_notations = [d["notation"] for d in deps.get("libraries", [])]
        check("retrofit library detected", any("retrofit" in n for n in lib_notations))

        # Manifest
        manifest = module.get("manifest", {})
        check("INTERNET permission", "android.permission.INTERNET" in manifest.get("permissions", []))
        components = manifest.get("components", [])
        check("LoginActivity in manifest", any("LoginActivity" in c.get("class", "") for c in components))
        exported = [c for c in components if "LoginActivity" in c.get("class", "")]
        check("LoginActivity exported=true", exported and exported[0].get("exported") is True)

        # Nodes
        nodes = {n["id"]: n for n in data.get("nodes", [])}
        check("has nodes", len(nodes) > 0, f"got {len(nodes)}")

        expected_nodes = [
            ("com.acme.login.ui.LoginViewModel", "class", "viewmodel"),
            ("com.acme.login.domain.LoginUseCase", "class", "usecase"),
            ("com.acme.login.domain.AuthRepository", "interface", "repository"),
            ("com.acme.login.data.AuthRepositoryImpl", "class", "repository"),
            ("com.acme.login.data.AuthApi", "interface", "api_service"),
            ("com.acme.login.di.LoginModule", "class", "di_module"),
            ("com.acme.login.data.SessionStore", "class", None),
            ("com.acme.login.ui.LoginActivity", "class", "android_entry"),
        ]
        for node_id, kind, role in expected_nodes:
            node = nodes.get(node_id)
            check(f"node {node_id.rsplit('.', 1)[-1]} exists", node is not None)
            if node:
                check(f"  kind={kind}", node["kind"] == kind, f"got {node['kind']}")
                if role:
                    check(f"  role={role}", node.get("role") == role, f"got {node.get('role')}")

        # Composable functions
        composables = [n for n in nodes.values() if n.get("role") == "composable"]
        composable_names = {n["name"] for n in composables}
        check("LoginScreen is composable", "LoginScreen" in composable_names)
        check("LoginForm is composable", "LoginForm" in composable_names)

        # Sealed interface and subtypes
        check("LoginUiState exists", "com.acme.login.ui.LoginUiState" in nodes)
        subtypes = [n for n in nodes.values() if n.get("parent") == "com.acme.login.ui.LoginUiState"]
        subtype_names = {n["name"] for n in subtypes}
        check("LoginUiState has 4 subtypes", len(subtypes) == 4, f"got {len(subtypes)}: {subtype_names}")

        # KDoc
        vm = nodes.get("com.acme.login.ui.LoginViewModel", {})
        check("ViewModel has KDoc", vm.get("doc") is not None and "flujo" in vm.get("doc", ""))
        submit = [f for f in vm.get("functions", []) if f["name"] == "submit"]
        check("submit() has KDoc", submit and submit[0].get("doc") is not None)

        # Constructor injection
        check("ViewModel constructor has login param",
              any(p["name"] == "login" for p in vm.get("constructor", [])))
        check("ViewModel @Inject constructor",
              any("Inject" in a for a in vm.get("constructor_annotations", [])))

        # Edges
        edges = data.get("edges", [])
        check("has edges", len(edges) > 0, f"got {len(edges)}")

        edge_set = {(e["from"], e["to"], e["kind"]) for e in edges}

        check("AuthRepositoryImpl implements AuthRepository",
              ("com.acme.login.data.AuthRepositoryImpl", "com.acme.login.domain.AuthRepository", "implements") in edge_set)
        check("LoginViewModel depends_on LoginUseCase",
              ("com.acme.login.ui.LoginViewModel", "com.acme.login.domain.LoginUseCase", "depends_on") in edge_set)
        check("LoginUseCase depends_on AuthRepository",
              ("com.acme.login.domain.LoginUseCase", "com.acme.login.domain.AuthRepository", "depends_on") in edge_set)
        check("LoginModule provides AuthRepository",
              ("com.acme.login.di.LoginModule", "com.acme.login.domain.AuthRepository", "provides") in edge_set)
        check("LoginModule provides AuthApi",
              ("com.acme.login.di.LoginModule", "com.acme.login.data.AuthApi", "provides") in edge_set)
        check("LoginViewModel extends ViewModel",
              any(e[0] == "com.acme.login.ui.LoginViewModel" and e[2] == "extends"
                  and "ViewModel" in e[1] for e in edge_set))
        check("LoginActivity extends ComponentActivity",
              any(e[0] == "com.acme.login.ui.LoginActivity" and e[2] == "extends"
                  and "ComponentActivity" in e[1] for e in edge_set))

        # DI detail
        di_edges = [e for e in edges if e["kind"] == "depends_on"
                    and any(d.get("di") for d in e.get("details", []))]
        check("DI edges have di=true", len(di_edges) >= 3, f"got {len(di_edges)}")

        # External nodes
        externals = {n["id"]: n for n in data.get("external_nodes", [])}
        check("has external nodes", len(externals) > 0)

        # Sources
        sources = data.get("sources", {})
        check("AST source recorded", sources.get("ast", {}).get("parser") == "tree-sitter-kotlin")
        check("codegraph skipped", sources.get("codegraph", {}).get("status") == "skipped")

        return data


# ─── module_diagrams.py against example maps ─────────────────────────────

def test_module_diagrams_single():
    print("\n=== module_diagrams.py (single module, example map) ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "output.md"
        run(DIAGRAM_SCRIPT, str(EXAMPLES / "feature-login.module-map.json"), "-o", str(out))

        check("output file created", out.is_file())
        text = out.read_text(encoding="utf-8")

        check("has title", "# :feature:login: arquitectura" in text)
        check("has layer diagram", "## Arquitectura por capas" in text)
        check("has mermaid block", "```mermaid" in text)
        check("has graph TD", "graph TD" in text)
        check("has flows section", "## Flujos desde los ViewModels" in text)
        check("has sequence section", "## Secuencia" in text)
        check("has module deps section", "## Dependencias entre módulos" in text)
        check("has DI section", "## Inyección de dependencias" in text)
        check("has classes table", "## Clases" in text)
        check("has violations section", "## Violaciones de capas" in text)
        check("has entry points", "## Puntos de entrada" in text)
        check("has external deps", "## Dependencias externas" in text)
        check("has review section", "## Aristas a revisar" in text)

        check("LoginViewModel in diagram", "LoginViewModel" in text)
        check("AuthRepository in diagram", "AuthRepository" in text)
        check("LoginUseCase in diagram", "LoginUseCase" in text)

        check("no violations detected", "No se encontraron violaciones" in text)

        check("LoginActivity in manifest table", "LoginActivity" in text)
        check("sequenceDiagram present", "sequenceDiagram" in text)


def test_module_diagrams_only():
    print("\n=== module_diagrams.py (--only classes) ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        run(DIAGRAM_SCRIPT, str(EXAMPLES / "feature-login.module-map.json"),
            "--only", "classes", "-o", str(out_dir / "partial.md"))

        out = out_dir / "partial.md"
        check("partial output created", out.is_file())
        text = out.read_text(encoding="utf-8")

        check("has classes section", "## Clases" in text)
        check("no layer diagram in partial", "## Arquitectura por capas" not in text)
        check("no flows in partial", "## Flujos desde los ViewModels" not in text)


def test_module_diagrams_sections_list():
    print("\n=== module_diagrams.py (--sections) ===\n")
    result = run(DIAGRAM_SCRIPT, "--sections")
    output = result.stdout
    for key in ("layers", "flows", "sequence", "modules", "hilt", "classes", "violations", "entries", "external", "review"):
        check(f"section '{key}' listed", key in output)


def test_module_diagrams_multi():
    print("\n=== module_diagrams.py (multiple modules from examples/) ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        run(DIAGRAM_SCRIPT, str(EXAMPLES), "-o", str(out_dir))

        files = list(out_dir.glob("*.md"))
        check("multiple output files", len(files) >= 3, f"got {len(files)}: {[f.name for f in files]}")
        check("index.md created", (out_dir / "index.md").is_file())

        index = (out_dir / "index.md").read_text(encoding="utf-8")
        check("index has module graph", "graph LR" in index)
        check("index has module table", ":feature:login" in index or "feature-login" in index)


# ─── module_map.py then module_diagrams.py end-to-end ─────────────────────

def test_end_to_end():
    print("\n=== End-to-end: module_map.py -> module_diagrams.py ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        map_out = Path(tmp) / "map.json"
        run(MAP_SCRIPT, str(FIXTURES / "feature" / "login"), "--no-codegraph", "-o", str(map_out))
        check("map JSON created", map_out.is_file())

        doc_out = Path(tmp) / "output.md"
        run(DIAGRAM_SCRIPT, str(map_out), "-o", str(doc_out))
        check("diagram doc created", doc_out.is_file())

        text = doc_out.read_text(encoding="utf-8")
        check("e2e: has title", ":feature:login: arquitectura" in text)
        check("e2e: has mermaid", "```mermaid" in text)
        check("e2e: LoginViewModel in output", "LoginViewModel" in text)
        check("e2e: has classes table", "## Clases" in text)
        check("e2e: has violations section", "## Violaciones de capas" in text)

        mermaid_count = text.count("```mermaid")
        check("e2e: multiple mermaid diagrams", mermaid_count >= 3, f"got {mermaid_count}")


# ─── edge cases ───────────────────────────────────────────────────────────

def test_no_kotlin_files():
    print("\n=== Edge case: no .kt files ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        empty_dir = Path(tmp) / "empty_module"
        empty_dir.mkdir()
        result = run(MAP_SCRIPT, str(empty_dir), "--no-codegraph", expect_ok=False)
        check("exits with error for empty dir", result.returncode != 0)
        check("error message mentions .kt", ".kt" in result.stderr or ".kt" in result.stdout)


def test_no_maps():
    print("\n=== Edge case: no maps for diagrams ===\n")
    with tempfile.TemporaryDirectory() as tmp:
        result = run(DIAGRAM_SCRIPT, str(tmp), expect_ok=False)
        check("exits with error for no maps", result.returncode != 0)


def test_invalid_section():
    print("\n=== Edge case: invalid --only section ===\n")
    result = run(DIAGRAM_SCRIPT, str(EXAMPLES / "feature-login.module-map.json"),
                 "--only", "nonexistent", expect_ok=False)
    check("exits with error for bad section", result.returncode != 0)
    check("mentions unknown section", "desconocida" in result.stderr or "desconocida" in result.stdout)


# ─── run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_module_map()
    test_module_diagrams_single()
    test_module_diagrams_only()
    test_module_diagrams_sections_list()
    test_module_diagrams_multi()
    test_end_to_end()
    test_no_kotlin_files()
    test_no_maps()
    test_invalid_section()

    print(f"\n{'=' * 50}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'=' * 50}")
    sys.exit(1 if failed else 0)
