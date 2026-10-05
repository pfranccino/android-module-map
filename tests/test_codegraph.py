"""module_map.load_codegraph against a hand-built codegraph index.

Each edge in the index exercises one rule of the edge validation: receiver type, import,
same package, discard, cross-module callers, test sources and heuristic edges.
"""
import json
import sqlite3

import pytest

import module_diagrams
import module_map

LOGIN = "feature/login/src/main/kotlin/com/acme/login"
VM = f"{LOGIN}/ui/LoginViewModel.kt"
USECASE = f"{LOGIN}/domain/LoginUseCase.kt"
DATA = f"{LOGIN}/data/AuthRepositoryImpl.kt"
SCREEN = f"{LOGIN}/ui/LoginScreen.kt"
CRYPTO = f"{LOGIN}/data/LegacyCrypto.java"
NAV = "app/src/main/kotlin/com/acme/app/MainNav.kt"
CLIENT = "core/network/src/main/kotlin/com/acme/network/ApiClient.kt"
LEGACY = "legacy/src/main/kotlin/com/acme/legacy/LoginUseCase.kt"
BILLING = "billing/src/main/kotlin/com/acme/billing/Guard.kt"
TEST_FILE = "feature/login/src/test/kotlin/com/acme/login/LoginTest.kt"

VIEWMODEL = "com.acme.login.ui.LoginViewModel"
LOGIN_USECASE = "com.acme.login.domain.LoginUseCase"
AUTH_REPOSITORY = "com.acme.login.domain.AuthRepository"
REPOSITORY_IMPL = "com.acme.login.data.AuthRepositoryImpl"


def line_of(root, path, text):
    for number, line in enumerate((root / path).read_text(encoding="utf-8").splitlines(), 1):
        if text in line:
            return number
    raise AssertionError(f"{text!r} not found in {path}")


def write_index(root):
    """.codegraph/codegraph.db with the tables and columns module_map.py reads (codegraph 1.6.1)."""
    def at(path, text):
        return line_of(root, path, text)

    def meta(ref, confidence=0.9, resolved_by="exact-match"):
        return json.dumps({"confidence": confidence, "resolvedBy": resolved_by, "refName": ref})

    nodes = [
        ("vm.submit", "method", "submit", "com.acme.login.ui::LoginViewModel::submit", VM, at(VM, "fun submit(")),
        ("usecase.invoke", "method", "invoke", "com.acme.login.domain::LoginUseCase::invoke", USECASE,
         at(USECASE, "operator fun invoke")),
        ("repo.login", "method", "login", "com.acme.login.domain::AuthRepository::login", USECASE,
         at(USECASE, "suspend fun login(email")),
        ("api.login", "method", "login", "com.acme.login.data::AuthApi::login", DATA, at(DATA, "suspend fun login(@Body")),
        ("impl.login", "method", "login", "com.acme.login.data::AuthRepositoryImpl::login", DATA,
         at(DATA, "override suspend fun login")),
        ("store.save", "method", "save", "com.acme.login.data::SessionStore::save", DATA, at(DATA, "fun save(session")),
        ("toSession", "function", "toSession", "com.acme.login.data::toSession", DATA, at(DATA, "fun LoginResponse.toSession")),
        ("screen", "function", "LoginScreen", "com.acme.login.ui::LoginScreen", SCREEN, at(SCREEN, "fun LoginScreen(")),
        ("crypto", "class", "LegacyCrypto", "com.acme.login.data::LegacyCrypto", CRYPTO, at(CRYPTO, "class LegacyCrypto")),
        ("nav", "function", "MainNav", "com.acme.app::MainNav", NAV, at(NAV, "fun MainNav")),
        ("client", "class", "ApiClient", "com.acme.network::ApiClient", CLIENT, at(CLIENT, "class ApiClient")),
        ("legacy", "class", "LoginUseCase", "com.acme.legacy::LoginUseCase", LEGACY, 3),
        ("guard", "class", "Guard", "com.acme.billing::Guard", BILLING, 3),
        ("guard.isAuthorized", "method", "isAuthorized", "com.acme.billing::Guard::isAuthorized", BILLING, 4),
        ("test.helper", "method", "helper", "com.acme.login::LoginTest::helper", TEST_FILE, 4),
    ]
    edges = [
        # `login(...)` calls the LoginUseCase property, but codegraph matched the namesake AuthApi.login.
        ("vm.submit", "api.login", "calls", at(VM, "login(email, password)"), meta("login"), None),
        ("usecase.invoke", "repo.login", "calls", at(USECASE, "repository.login"), meta("repository.login"), None),
        ("impl.login", "api.login", "calls", at(DATA, "api.login("), meta("api.login"), None),
        ("impl.login", "store.save", "calls", at(DATA, "store.save("), meta("store.save"), None),
        # Same package, nothing names the target: kept, but only with codegraph's confidence.
        ("impl.login", "toSession", "calls", at(DATA, ".toSession()"), meta("toSession", 0.7, "instance-method"), None),
        # The file imports com.acme.login.domain.LoginUseCase; codegraph picked a namesake in another module.
        ("vm.submit", "legacy", "references", at(VM, "login(email, password)"), meta("LoginUseCase"), None),
        # Nothing in the calling file supports this target: discarded.
        ("impl.login", "guard.isAuthorized", "calls", at(DATA, "override suspend fun login"), meta("isAuthorized"), None),
        ("nav", "screen", "calls", at(NAV, "LoginScreen(onLoggedIn"), meta("LoginScreen"), None),
        ("test.helper", "usecase.invoke", "calls", 5, meta("invoke"), None),
        ("repo.login", "impl.login", "calls", at(USECASE, "suspend fun login(email"), meta("login"), "heuristic"),
        ("guard", "guard.isAuthorized", "contains", 4, None, None),
    ]
    db = root / ".codegraph" / "codegraph.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
                            start_line INTEGER);
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT, line INTEGER, metadata TEXT, provenance TEXT);
        CREATE TABLE project_metadata (key TEXT, value TEXT);
        INSERT INTO project_metadata VALUES ('indexed_with_version', '1.6.1');
    """)
    con.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)", nodes)
    con.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)", edges)
    con.commit()
    con.close()


@pytest.fixture
def mapped(project, monkeypatch):
    monkeypatch.setattr(module_map.shutil, "which", lambda name: None)  # a real codegraph would re-index the fake db
    write_index(project)
    _, result, _ = module_map.map_module(project / "feature" / "login", project, True, {"status": "skipped"})
    return result


def edge(result, source, target, kind):
    return next((e for e in result["edges"] if (e["from"], e["to"], e["kind"]) == (source, target, kind)), None)


def test_index_is_read(mapped):
    codegraph = mapped["sources"]["codegraph"]
    assert codegraph["status"] == "ok"
    assert codegraph["version"] == "1.6.1"
    assert codegraph["edges_used"] == 7
    assert codegraph["edges_discarded"] == 1


def test_receiver_type_overrides_a_namesake(mapped):
    call = edge(mapped, VIEWMODEL, LOGIN_USECASE, "calls")
    assert call is not None
    assert set(call["provenance"]) == {"codegraph", "ast"}
    detail = call["details"][0]
    assert detail["resolved_by"] == "ast_receiver_type"
    assert (detail["from"], detail["to"]) == ("submit", "invoke")
    assert detail["corrected_from"] == "com.acme.login.data.AuthApi"
    assert edge(mapped, VIEWMODEL, "com.acme.login.data.AuthApi", "calls") is None


def test_receiver_type_confirms_the_target(mapped):
    call = edge(mapped, LOGIN_USECASE, AUTH_REPOSITORY, "calls")
    assert call["details"][0]["resolved_by"] == "ast_receiver_type"
    assert "corrected_from" not in call["details"][0]


def test_import_overrides_a_namesake(mapped):
    reference = edge(mapped, VIEWMODEL, LOGIN_USECASE, "references")
    assert reference["details"][0]["resolved_by"] == "ast_import"
    assert reference["details"][0]["corrected_from"] == "com.acme.legacy.LoginUseCase"
    assert not any("legacy" in n["id"] for n in mapped["nodes"] + mapped["external_nodes"])


def test_same_package_call_stays_unconfirmed(mapped):
    call = edge(mapped, REPOSITORY_IMPL, "com.acme.login.data.toSession", "calls")
    assert call["provenance"] == ["codegraph"]
    assert call["details"][0]["confidence"] == 0.7


def test_unsupported_edge_is_discarded(mapped):
    assert not any("billing" in e["to"] for e in mapped["edges"])


def test_caller_in_another_module(mapped):
    call = edge(mapped, "com.acme.app.MainNav", "com.acme.login.ui.LoginScreen", "calls")
    assert "ast" in call["provenance"]
    caller = next(n for n in mapped["external_nodes"] if n["id"] == "com.acme.app.MainNav")
    assert (caller["origin"], caller["module"]) == ("project", ":app")


def test_test_sources_and_heuristic_edges_are_skipped(mapped):
    assert not any("LoginTest" in e["from"] for e in mapped["edges"])
    assert edge(mapped, AUTH_REPOSITORY, REPOSITORY_IMPL, "calls") is None


def test_import_of_another_module_becomes_a_project_external(mapped):
    client = next(n for n in mapped["external_nodes"] if n["id"] == "com.acme.network.ApiClient")
    assert (client["origin"], client["module"]) == ("project", ":core:network")


def test_type_the_ast_could_not_place_is_found_in_the_index(mapped):
    crypto = next(n for n in mapped["nodes"] if n["id"] == "com.acme.login.data.LegacyCrypto")
    assert crypto["source"] == "codegraph"
    assert edge(mapped, "com.acme.login.data.SessionStore", crypto["id"], "uses_type") is not None


def test_missing_codegraph_binary_is_reported(mapped):
    assert any("codegraph is not on the PATH" in w for w in mapped["warnings"])


def test_index_is_synced_when_codegraph_is_installed(project, monkeypatch):
    write_index(project)
    calls = []
    monkeypatch.setattr(module_map.shutil, "which", lambda name: "codegraph-bin")
    monkeypatch.setattr(module_map.subprocess, "run", lambda command, **kwargs: calls.append(command))
    _, result, _ = module_map.map_module(project / "feature" / "login", project, True, {"status": "skipped"})
    assert calls == [["codegraph-bin", "sync", "--quiet", str(project)]]
    assert not any("PATH" in w for w in result.get("warnings", []))


def test_document_from_a_map_with_calls(mapped):
    text, _ = module_diagrams.build(module_diagrams.Map(mapped, lang="es"), None, list(module_diagrams.SECTIONS))
    assert "## Secuencia: LoginViewModel.submit" in text
    assert '-->|"submit"|' in text
    assert "| LoginScreen | MainNav | :app | calls |" in text
    assert "Corregida: `LoginViewModel → LoginUseCase`" in text
    assert "Confianza 0.7" in text
    assert "Sin AST (solo datos de codegraph): `LegacyCrypto`" in text
    assert "aristas descartadas: 1" in text
