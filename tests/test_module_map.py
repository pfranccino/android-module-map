"""module_map.py in process: helpers, Gradle and catalog reading, and the AST map of the fixture."""
import string
import subprocess
import sys

import pytest

import module_map
from conftest import FIXTURES, REPO

LOGIN_DIR = FIXTURES / "feature" / "login"


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def keys(tree, prefix=""):
    found = set()
    for key, value in tree.items():
        found.add(prefix + key)
        if isinstance(value, dict):
            found |= keys(value, prefix + key + ".")
    return found


@pytest.fixture(scope="module")
def login_map():
    catalog, _ = module_map.read_version_catalog(FIXTURES, "es")
    _, result, _ = module_map.map_module(LOGIN_DIR, FIXTURES, False, {"status": "skipped"}, catalog=catalog)
    return result


@pytest.fixture(scope="module")
def nodes(login_map):
    return {n["id"]: n for n in login_map["nodes"]}


@pytest.fixture(scope="module")
def edges(login_map):
    return {(e["from"], e["to"], e["kind"]) for e in login_map["edges"]}


# ---------- importing ----------

def test_import_needs_neither_tree_sitter_nor_network():
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import module_map; "
             "assert module_map.PARSER is None; assert 'tree_sitter' not in sys.modules")
    subprocess.run([sys.executable, "-c", probe, str(REPO / "scripts")], check=True)


def test_messages_have_the_same_keys_and_placeholders_in_every_language():
    es, en = module_map.MESSAGES["es"], module_map.MESSAGES["en"]
    assert es.keys() == en.keys()
    for key in es:
        assert placeholders(es[key]) == placeholders(en[key]), key


def test_legend_has_the_same_keys_in_every_language():
    assert keys(module_map.LEGEND["es"]) == keys(module_map.LEGEND["en"])


# ---------- helpers ----------

@pytest.mark.parametrize("a, b, expected", [
    ("com.a.B", "com.a.B", True),
    ("com.a.B", "com.a.B.inner", True),
    ("com.a.B.inner", "com.a.B", True),
    ("com.a.B", "com.a.Bc", False),
])
def test_related(a, b, expected):
    assert module_map.related(a, b) is expected


@pytest.mark.parametrize("text, expected", [
    ("Foo", "Foo"), ("Foo<Bar>?", "Foo"), ("com.a.Foo", "com.a.Foo"), ("() -> Unit", None), (None, None),
])
def test_nominal(text, expected):
    assert module_map.nominal(text) == expected


def test_ann_names():
    assert module_map.ann_names(['field:Inject', 'POST("x")', "dagger.Provides"]) == {"Inject", "POST", "Provides"}


@pytest.mark.parametrize("path, expected", [
    ("feature/src/test/kotlin/A.kt", True),
    ("feature/src/androidTest/kotlin/A.kt", True),
    ("feature/src/testDebug/kotlin/A.kt", True),
    ("feature/src/main/kotlin/A.kt", False),
    ("feature/test/src/main/A.kt", False),
])
def test_in_test_source_set(path, expected):
    assert module_map.in_test_source_set(path) is expected


def test_compact_drops_empty_values_but_keeps_false_and_zero():
    assert module_map.compact({"a": None, "b": [], "c": {}, "d": False, "e": 0, "f": [{"g": None}]}) == \
        {"d": False, "e": 0, "f": [{}]}


@pytest.mark.parametrize("node, role", [
    ({"name": "LoginActivity", "annotations": ["AndroidEntryPoint"], "supertypes": ["ComponentActivity"]},
     "android_entry"),
    ({"name": "Home", "supertypes": ["ViewModel"]}, "viewmodel"),
    ({"name": "Api", "functions": [{"annotations": ['GET("users")']}]}, "api_service"),
    ({"name": "UserRepositoryImpl"}, "repository"),
    ({"name": "LoadUserUseCase"}, "usecase"),
    ({"name": "Helper"}, None),
])
def test_role_of(node, role):
    assert module_map.role_of(node) == role


@pytest.mark.parametrize("imports, layer", [
    ({"Retrofit": "retrofit2.Retrofit", "Call": "okhttp3.Call"}, "Data"),
    ({"Composable": "androidx.compose.runtime.Composable"}, "Presentation"),
    ({"Retrofit": "retrofit2.Retrofit", "Composable": "androidx.compose.runtime.Composable"}, None),
    ({"List": "kotlin.collections.List"}, None),
])
def test_layer_from_imports(imports, layer):
    assert module_map.layer_from_imports(imports) == layer


# ---------- Gradle, version catalog, Manifest ----------

def test_version_catalog():
    catalog, warning = module_map.read_version_catalog(FIXTURES, "es")
    assert warning is None
    assert catalog["libraries"] == {
        "retrofit": "com.squareup.retrofit2:retrofit:2.11.0",
        "okhttp": "com.squareup.okhttp3:okhttp:4.12.0",
        "androidx.lifecycle.viewmodel": "androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7",
        "compose.bom": "androidx.compose:compose-bom:2024.10.00",
        "compose.ui": "androidx.compose.ui:ui",
    }
    assert catalog["bundles"] == {"network": ["com.squareup.retrofit2:retrofit:2.11.0",
                                              "com.squareup.okhttp3:okhttp:4.12.0"]}
    assert catalog["plugins"] == {"android.library": "com.android.library",
                                  "kotlin.android": "org.jetbrains.kotlin.android"}


def test_version_catalog_absent(tmp_path):
    assert module_map.read_version_catalog(tmp_path, "es") == (None, None)


def test_version_catalog_invalid(tmp_path):
    (tmp_path / "gradle").mkdir()
    (tmp_path / "gradle" / "libs.versions.toml").write_text("[libraries\n", encoding="utf-8")
    catalog, warning = module_map.read_version_catalog(tmp_path, "en")
    assert catalog is None
    assert "gradle/libs.versions.toml is not valid TOML" in warning


def test_read_gradle_resolves_catalog_notations():
    catalog, _ = module_map.read_version_catalog(FIXTURES, "es")
    gradle = module_map.read_gradle(LOGIN_DIR, catalog)
    assert gradle["type"] == "android-library"
    assert gradle["namespace"] == "com.acme.login"
    assert {(d["path"], d["configuration"]) for d in gradle["dependencies"]["modules"]} == \
        {(":core:network", "implementation"), (":core:model", "api")}
    resolved = {d["notation"]: d.get("resolved") for d in gradle["dependencies"]["libraries"]}
    assert resolved == {
        "com.squareup.retrofit2:retrofit:2.11.0": None,
        "libs.androidx.lifecycle.viewmodel": "androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7",
        "platform(libs.compose.bom)": "androidx.compose:compose-bom:2024.10.00",
        "libs.compose.ui": "androidx.compose.ui:ui",
        "junit:junit:4.13.2": None,
    }


def test_read_gradle_resolves_bundles():
    catalog, _ = module_map.read_version_catalog(FIXTURES, "es")
    gradle = module_map.read_gradle(FIXTURES / "core" / "network", catalog)
    assert gradle["dependencies"]["libraries"][0]["resolved"] == ["com.squareup.retrofit2:retrofit:2.11.0",
                                                                   "com.squareup.okhttp3:okhttp:4.12.0"]


def test_module_type_comes_from_the_catalog_plugin_id(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("plugins {\n    alias(libs.plugins.agp.lib)\n}\n", encoding="utf-8")
    catalog = {"libraries": {}, "bundles": {}, "plugins": {"agp.lib": "com.android.library"}}
    assert module_map.read_gradle(tmp_path, catalog)["type"] == "android-library"
    assert module_map.read_gradle(tmp_path)["type"] is None


def test_read_settings():
    assert module_map.read_settings(FIXTURES) == {"name": "TestProject",
                                                  "modules": [":app", ":core:network", ":feature:login"]}


def test_read_manifest():
    manifest = module_map.read_manifest(LOGIN_DIR / "src" / "main" / "AndroidManifest.xml", "com.acme.login")
    assert manifest == {"permissions": ["android.permission.INTERNET"],
                        "components": [{"type": "activity", "class": "com.acme.login.ui.LoginActivity",
                                        "exported": True}]}


def test_malformed_manifest_becomes_a_warning(project):
    (project / "feature" / "login" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest", encoding="utf-8")
    _, result, _ = module_map.map_module(project / "feature" / "login", project, False, {"status": "skipped"},
                                         lang="en")
    assert "manifest" not in result["module"]
    assert any(w.startswith("malformed AndroidManifest.xml") for w in result["warnings"])


def test_inner_modules_and_gradle_paths():
    assert [p.relative_to(FIXTURES).as_posix() for p in module_map.inner_modules(FIXTURES)] == \
        ["app", "core/network", "feature/login"]
    assert module_map.gradle_module(LOGIN_DIR / "src" / "main", FIXTURES) == (LOGIN_DIR, ":feature:login")


# ---------- the AST map ----------

def test_map_header(login_map):
    assert login_map["schema"] == "module-map/1"
    assert login_map["project"]["name"] == "TestProject"
    assert login_map["module"]["path"] == ":feature:login"
    assert login_map["module"]["manifest"]["components"][0]["exported"] is True
    assert login_map["sources"]["ast"] == {"parser": "tree-sitter-kotlin", "files": 6}
    assert login_map["sources"]["codegraph"] == {"status": "skipped"}
    assert login_map["legend"] == module_map.LEGEND["es"]


@pytest.mark.parametrize("node_id, kind, role", [
    ("com.acme.login.ui.LoginViewModel", "class", "viewmodel"),
    ("com.acme.login.ui.LoginActivity", "class", "android_entry"),
    ("com.acme.login.ui.LoginScreen", "function", "composable"),
    ("com.acme.login.ui.LoginForm", "function", "composable"),
    ("com.acme.login.domain.LoginUseCase", "class", "usecase"),
    ("com.acme.login.domain.AuthRepository", "interface", "repository"),
    ("com.acme.login.data.AuthRepositoryImpl", "class", "repository"),
    ("com.acme.login.data.AuthApi", "interface", "api_service"),
    ("com.acme.login.di.LoginModule", "class", "di_module"),
    ("com.acme.login.data.SessionStore", "class", None),
])
def test_declarations(nodes, node_id, kind, role):
    assert nodes[node_id]["kind"] == kind
    assert nodes[node_id].get("role") == role


def test_nested_sealed_subtypes(nodes):
    subtypes = {n["name"] for n in nodes.values() if n.get("parent") == "com.acme.login.ui.LoginUiState"}
    assert subtypes == {"Idle", "Loading", "Success", "Error"}
    assert "sealed" in nodes["com.acme.login.ui.LoginUiState"]["modifiers"]


def test_kdoc_and_constructor(nodes):
    viewmodel = nodes["com.acme.login.ui.LoginViewModel"]
    assert viewmodel["doc"] == "Coordina el flujo de inicio de sesión y expone el estado a la UI."
    assert next(f for f in viewmodel["functions"] if f["name"] == "submit")["doc"] == \
        "Lanza el login con las credenciales dadas."
    assert viewmodel["constructor"] == [{"name": "login", "type": "LoginUseCase"}]
    assert viewmodel["constructor_annotations"] == ["Inject"]


@pytest.mark.parametrize("source, target, kind", [
    ("com.acme.login.data.AuthRepositoryImpl", "com.acme.login.domain.AuthRepository", "implements"),
    ("com.acme.login.ui.LoginViewModel", "com.acme.login.domain.LoginUseCase", "depends_on"),
    ("com.acme.login.domain.LoginUseCase", "com.acme.login.domain.AuthRepository", "depends_on"),
    ("com.acme.login.data.AuthRepositoryImpl", "com.acme.network.ApiClient", "depends_on"),
    ("com.acme.login.di.LoginModule", "com.acme.login.domain.AuthRepository", "provides"),
    ("com.acme.login.di.LoginModule", "com.acme.login.data.AuthApi", "provides"),
    ("com.acme.login.ui.LoginViewModel", "androidx.lifecycle.ViewModel", "extends"),
    ("com.acme.login.ui.LoginActivity", "androidx.activity.ComponentActivity", "extends"),
])
def test_relationships(edges, source, target, kind):
    assert (source, target, kind) in edges


def test_injected_constructors_are_marked(login_map):
    injected = [e for e in login_map["edges"] if e["kind"] == "depends_on" and any(d.get("di") for d in e["details"])]
    assert len(injected) == 5


def test_java_class_is_invisible_without_codegraph(nodes):
    assert "com.acme.login.data.LegacyCrypto" not in nodes


def test_external_nodes(login_map):
    externals = {n["id"]: n["origin"] for n in login_map["external_nodes"]}
    assert externals["com.acme.network.ApiClient"] == "library"  # only codegraph knows it is a project module
    assert externals["androidx.lifecycle.ViewModel"] == "library"


def test_libraries_are_resolved_in_the_map(login_map):
    libraries = login_map["module"]["dependencies"]["libraries"]
    assert {"notation": "libs.compose.ui", "configuration": "implementation",
            "resolved": "androidx.compose.ui:ui"} in libraries
