"""Both scripts run as commands, the way users and the Claude Code skill run them."""
import json
import os
import subprocess
import sys

from conftest import FIXTURES, REPO

MAP_SCRIPT = REPO / "scripts" / "module_map.py"
DIAGRAM_SCRIPT = REPO / "scripts" / "module_diagrams.py"
EXAMPLES = REPO / "examples"


def run(script, *args, env=None):
    clean = {k: v for k, v in os.environ.items() if k != "MODULE_MAP_LANG"}
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, encoding="utf-8",
                          timeout=300, env={**clean, "PYTHONIOENCODING": "utf-8", **(env or {})})


def ok(result):
    assert result.returncode == 0, result.stderr
    return result


def map_and_document(tmp_path, *extra):
    ok(run(MAP_SCRIPT, FIXTURES / "feature" / "login", "--no-codegraph", "-o", tmp_path / "map.json", *extra))
    ok(run(DIAGRAM_SCRIPT, tmp_path / "map.json", "-o", tmp_path / "doc.md", *extra))
    return json.loads((tmp_path / "map.json").read_text(encoding="utf-8")), \
        (tmp_path / "doc.md").read_text(encoding="utf-8")


def test_end_to_end_in_english_by_default(tmp_path):
    data, text = map_and_document(tmp_path)
    assert data["module"]["path"] == ":feature:login"
    assert data["legend"]["edge_kinds"]["calls"] == "function or method call"
    assert text.startswith("# :feature:login: architecture")
    for heading in ("## Layered architecture", "## Classes", "## Layer violations", "## Edges to review"):
        assert heading in text
    assert text.count("```mermaid") >= 3
    assert "classDef layerPresentation" in text
    assert "codegraph was not used (skipped)" in text


def test_single_module_gets_a_page(tmp_path):
    map_and_document(tmp_path)
    page = (tmp_path / "doc.html").read_text(encoding="utf-8")
    assert "<title>:feature:login</title>" in page
    assert "```mermaid" in page


def test_end_to_end_in_spanish(tmp_path):
    data, text = map_and_document(tmp_path, "--lang", "es")
    assert data["legend"]["edge_kinds"]["calls"] == "llamada a función o método"
    assert text.startswith("# :feature:login: arquitectura")
    for heading in ("## Arquitectura por capas", "## Clases", "## Violaciones de capas", "## Aristas a revisar"):
        assert heading in text


def test_language_from_the_environment(tmp_path):
    ok(run(MAP_SCRIPT, FIXTURES / "feature" / "login", "--no-codegraph", "-o", tmp_path / "map.json"))
    result = ok(run(DIAGRAM_SCRIPT, tmp_path / "map.json", "-o", tmp_path / "doc.md", env={"MODULE_MAP_LANG": "es"}))
    assert "generado:" in result.stderr
    assert "## Arquitectura por capas" in (tmp_path / "doc.md").read_text(encoding="utf-8")


def test_language_and_rules_from_the_config_file(project):
    (project / ".module-map.toml").write_text(
        'lang = "es"\n\n[layers]\nforbidden = [["Presentation", "Domain"]]\n', encoding="utf-8")
    module = project / "feature" / "login"
    result = ok(run(MAP_SCRIPT, module, "--no-codegraph"))
    assert "parseando 6 archivos" in result.stderr
    ok(run(DIAGRAM_SCRIPT, module))
    docs = module / "docs" / "architecture"
    assert json.loads((docs / "feature-login.module-map.json").read_text(encoding="utf-8"))["legend"]["weight"] == \
        "número de apariciones agregadas en la arista"
    text = (docs / "feature-login.md").read_text(encoding="utf-8")
    assert "| LoginViewModel → LoginUseCase | Presentation → Domain | depends_on |" in text


def test_flag_wins_over_the_config_file(project):
    (project / ".module-map.toml").write_text('lang = "en"\n', encoding="utf-8")
    module = project / "feature" / "login"
    ok(run(MAP_SCRIPT, module, "--no-codegraph", "--lang", "es"))
    ok(run(DIAGRAM_SCRIPT, module, "--lang", "es"))
    assert "## Arquitectura por capas" in (module / "docs" / "architecture" / "feature-login.md").read_text(
        encoding="utf-8")


def test_invalid_config_is_rejected(project):
    (project / ".module-map.toml").write_text('[layers.by_segment]\nui = "View"\n', encoding="utf-8")
    result = run(DIAGRAM_SCRIPT, EXAMPLES / "feature-login.module-map.json", "--config",
                 project / ".module-map.toml", "-o", project / "doc.md")
    assert result.returncode == 1
    assert "invalid value for `layers.by_segment.ui`: 'View'" in result.stderr


def test_whole_project(project):
    result = ok(run(MAP_SCRIPT, project, "--no-codegraph"))
    assert "3 maps written, each one inside its module" in result.stderr
    ok(run(DIAGRAM_SCRIPT, project))
    for module, name in (("app", "app"), ("core/network", "core-network"), ("feature/login", "feature-login")):
        assert (project / module / "docs" / "architecture" / f"{name}.md").is_file()
    index = (project / "docs" / "architecture" / "index.md").read_text(encoding="utf-8")
    assert "# TestProject: modules" in index
    assert "[:app](../../app/docs/architecture/app.md)" in index
    assert 'href "../../app/docs/architecture/app.md"' in index
    page = (project / "docs" / "architecture" / "index.html").read_text(encoding="utf-8")
    assert '"file": "../../app/docs/architecture/app.md"' in page


def test_no_timestamp_regenerates_without_a_diff(tmp_path):
    def generate(name):
        ok(run(MAP_SCRIPT, FIXTURES / "feature" / "login", "--no-codegraph", "--no-timestamp",
               "-o", tmp_path / f"{name}.json"))
        ok(run(DIAGRAM_SCRIPT, tmp_path / f"{name}.json", "-o", tmp_path / f"{name}.md"))
        return [(tmp_path / f"{name}.{ext}").read_text(encoding="utf-8") for ext in ("json", "md")]

    first, second = generate("first"), generate("second")
    assert first == second
    assert "generated_at" not in first[0]
    assert "from the map. " in first[1]


def test_whole_project_resolves_the_version_catalog(project):
    ok(run(MAP_SCRIPT, project, "--no-codegraph"))
    data = json.loads((project / "core" / "network" / "docs" / "architecture" / "core-network.module-map.json")
                      .read_text(encoding="utf-8"))
    assert data["module"]["type"] == "android-library"
    assert data["module"]["dependencies"]["libraries"][0]["resolved"] == [
        "com.squareup.retrofit2:retrofit:2.11.0", "com.squareup.okhttp3:okhttp:4.12.0"]


def test_examples_directory(tmp_path):
    ok(run(DIAGRAM_SCRIPT, EXAMPLES, "-o", tmp_path))
    assert sorted(p.name for p in tmp_path.glob("*.md")) == ["app.md", "core-network.md", "feature-login.md",
                                                            "index.md"]
    assert (tmp_path / "index.html").is_file()
    assert sorted(p.name for p in tmp_path.glob("*.html")) == ["index.html"]  # modules are inside the index


def test_only_one_section(tmp_path):
    ok(run(DIAGRAM_SCRIPT, EXAMPLES, "--module", "login", "--only", "classes", "-o", tmp_path))
    text = (tmp_path / "feature-login.classes.md").read_text(encoding="utf-8")
    assert "## Classes" in text
    assert "## Layered architecture" not in text
    assert (tmp_path / "feature-login.classes.html").is_file()


def test_sections_are_listed_in_both_languages():
    assert "violaciones de capas" in ok(run(DIAGRAM_SCRIPT, "--sections", "--lang", "es")).stdout
    english = ok(run(DIAGRAM_SCRIPT, "--sections")).stdout
    for key in ("layers", "flows", "sequence", "modules", "hilt", "classes", "violations", "entries", "external",
                "review"):
        assert key in english
    assert "layer violations" in english


def test_help_follows_the_language():
    assert "do not read the codegraph index" in ok(run(MAP_SCRIPT, "--help")).stdout
    assert "no leer el índice de codegraph" in ok(run(MAP_SCRIPT, "--lang", "es", "--help")).stdout


def test_module_without_kotlin_files(tmp_path):
    result = run(MAP_SCRIPT, tmp_path, "--no-codegraph")
    assert result.returncode == 1
    assert "No .kt files" in result.stderr


def test_directory_without_maps(tmp_path):
    result = run(DIAGRAM_SCRIPT, tmp_path)
    assert result.returncode == 1
    assert "No maps in" in result.stderr


def test_unknown_section():
    result = run(DIAGRAM_SCRIPT, EXAMPLES / "feature-login.module-map.json", "--only", "nonexistent")
    assert result.returncode == 1
    assert "Unknown section: nonexistent" in result.stderr


def test_unsupported_language():
    assert run(DIAGRAM_SCRIPT, "--sections", "--lang", "fr").returncode == 2  # rejected by argparse
    result = run(DIAGRAM_SCRIPT, "--sections", env={"MODULE_MAP_LANG": "fr"})
    assert result.returncode == 1
    assert "Unsupported language: 'fr'" in result.stderr
