#!/usr/bin/env python3
"""
module_map.py: JSON map (nodes + relationships) of a Kotlin/Android module,
meant as input for a script or an LLM to diagram and document it.

Sources and what each one contributes:
  1. AST (tree-sitter-kotlin): declarations, signatures, KDoc, annotations, inheritance,
     constructor dependencies and Hilt bindings. It is syntactic: it does not see
     inferred types or resolve calls.
  2. codegraph (.codegraph/codegraph.db): calls, instantiations and references,
     including those that cross the module boundary. It resolves by name, so
     every edge carries its confidence. Tested with @colbymchenry/codegraph 1.6.1.
  3. Gradle + AndroidManifest: module type, dependencies between modules, components. The
     libs.* notations are resolved with the version catalog, gradle/libs.versions.toml.
  4. Android CLI (`android describe`): build metadata of the project.

Usage: python module_map.py <module_directory> [--lang es]
       python module_map.py <directory_with_several_modules>

Each map is written inside its own module, in <module>/docs/architecture/. Pass -o to collect
them in one directory instead. Messages and the legend inside the map follow --lang, then the
MODULE_MAP_LANG variable, then `lang` in .module-map.toml; English by default.
Deps:  tree-sitter and tree-sitter-kotlin. If they are not installed, the script creates its own
       environment in ~/.cache/module-map/venv, installs them there and re-runs itself inside it.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import venv
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Versions the script was tested with: the grammar defines the node names it reads.
REQUIREMENTS = ("tree-sitter==0.26.0", "tree-sitter-kotlin==1.1.0", 'tomli>=2.0.1; python_version < "3.11"')
VENV = Path.home() / ".cache" / "module-map" / "venv"
LANGS = ("es", "en")
DEFAULT_LANG = "en"
CONFIG_FILE = ".module-map.toml"
SETTINGS_FILES = ("settings.gradle.kts", "settings.gradle")

MESSAGES = {
    "es": {
        "deps_unimportable": "Las dependencias no se pueden importar desde {venv}. Borra ese directorio y reintenta.",
        "python_too_old": "module_map.py necesita Python 3.10 o superior.",
        "first_run": "[module_map] primera ejecución: instalando {requirements} en {venv}...",
        "venv_failed": "No se pudo preparar el entorno ({error}). Hace falta conexión para la primera "
                       "instalación y, en Debian/Ubuntu, el paquete python3-venv.",
        "partial_parse": "parse parcial, puede faltar alguna declaración: {file}",
        "codegraph_hint": "ejecuta `codegraph init` en la raíz del proyecto",
        "codegraph_sync": "codegraph sync (incremental)...",
        "codegraph_not_in_path": "codegraph no está en el PATH: se usó el índice tal cual, puede estar desactualizado",
        "describe_running": "android describe (ejecuta Gradle, máximo {minutes} min)...",
        "describe_timeout": "android describe superó el límite: el mapa se escribe sin esos metadatos",
        "manifest_invalid": "AndroidManifest.xml mal formado, se omite: {file} ({error})",
        "catalog_no_toml": "no se leyó {file}: hace falta Python 3.11+ o el paquete tomli, así que las "
                           "dependencias libs.* quedan sin resolver",
        "catalog_invalid": "{file} no es TOML válido, las dependencias libs.* quedan sin resolver ({error})",
        "dependency_unparsed": "{file}: dependencia sin interpretar, no aparece en el mapa: `{declaration}`",
        "parsing": "{module}: parseando {count} archivos...",
        "summary": "{nodes} nodos, {externals} externos, {edges} aristas, {warnings} avisos",
        "no_dir": "No existe el directorio: {path}",
        "no_kotlin": "No hay archivos .kt en {path}",
        "output_not_json": "{path} contiene {count} módulos: -o debe ser un directorio, no un archivo .json",
        "modules_found": "módulos Gradle encontrados en {path}: {count}",
        "module_skipped": "{path}: sin archivos .kt propios, se omite",
        "no_module_has_kotlin": "Ningún módulo de {path} tiene archivos .kt",
        "maps_written_to": "{count} mapas escritos en {path}",
        "maps_written_each": "{count} mapas escritos, cada uno en su módulo",
        "config_not_found": "No existe el archivo de configuración: {path}",
        "config_no_toml": "{file} necesita Python 3.11+ o el paquete tomli para leerse",
        "config_invalid_toml": "{file} no es TOML válido: {error}",
        "bad_lang": "Idioma no soportado: {value!r}. Disponibles: {langs}",
        "cli_description": "Mapa JSON de un módulo Kotlin/Android para diagramar con un LLM.",
        "help_module_dir": "directorio de un módulo, de un paquete dentro de él, o que contiene varios módulos",
        "help_output": "directorio donde reunir los JSON (por defecto, cada uno en <módulo>/docs/architecture); "
                       "con un solo módulo también puede ser el archivo .json de salida",
        "help_no_codegraph": "no leer el índice de codegraph",
        "help_android_cli": "ejecutar `android describe` y adjuntar sus metadatos de build (lanza Gradle, puede tardar)",
        "help_no_timestamp": "no escribir generated_at, para que regenerar sin cambios no produzca diff "
                             "(otra opción: definir SOURCE_DATE_EPOCH, que fija la fecha)",
        "help_lang": "idioma de los mensajes y de la leyenda del mapa; si no se indica, MODULE_MAP_LANG, "
                     "luego `lang` en .module-map.toml, luego es",
        "help_config": "archivo .module-map.toml; por defecto se busca desde el módulo hasta la raíz del proyecto",
    },
    "en": {
        "deps_unimportable": "The dependencies cannot be imported from {venv}. Delete that directory and retry.",
        "python_too_old": "module_map.py needs Python 3.10 or later.",
        "first_run": "[module_map] first run: installing {requirements} in {venv}...",
        "venv_failed": "Could not prepare the environment ({error}). The first installation needs network "
                       "access and, on Debian/Ubuntu, the python3-venv package.",
        "partial_parse": "partial parse, a declaration may be missing: {file}",
        "codegraph_hint": "run `codegraph init` at the project root",
        "codegraph_sync": "codegraph sync (incremental)...",
        "codegraph_not_in_path": "codegraph is not on the PATH: the index was used as is and may be out of date",
        "describe_running": "android describe (runs Gradle, at most {minutes} min)...",
        "describe_timeout": "android describe hit the time limit: the map is written without that metadata",
        "manifest_invalid": "malformed AndroidManifest.xml, skipped: {file} ({error})",
        "catalog_no_toml": "{file} was not read: it needs Python 3.11+ or the tomli package, so the libs.* "
                           "dependencies stay unresolved",
        "catalog_invalid": "{file} is not valid TOML, the libs.* dependencies stay unresolved ({error})",
        "dependency_unparsed": "{file}: dependency not understood, it is left out of the map: `{declaration}`",
        "parsing": "{module}: parsing {count} files...",
        "summary": "{nodes} nodes, {externals} externals, {edges} edges, {warnings} warnings",
        "no_dir": "Directory not found: {path}",
        "no_kotlin": "No .kt files in {path}",
        "output_not_json": "{path} contains {count} modules: -o must be a directory, not a .json file",
        "modules_found": "Gradle modules found in {path}: {count}",
        "module_skipped": "{path}: no .kt files of its own, skipped",
        "no_module_has_kotlin": "No module in {path} has .kt files",
        "maps_written_to": "{count} maps written to {path}",
        "maps_written_each": "{count} maps written, each one inside its module",
        "config_not_found": "Configuration file not found: {path}",
        "config_no_toml": "{file} needs Python 3.11+ or the tomli package to be read",
        "config_invalid_toml": "{file} is not valid TOML: {error}",
        "bad_lang": "Unsupported language: {value!r}. Available: {langs}",
        "cli_description": "JSON map of a Kotlin/Android module, to diagram it with a script or an LLM.",
        "help_module_dir": "directory of a module, of a package inside one, or that contains several modules",
        "help_output": "directory to collect the JSON files in (by default, each one goes to "
                       "<module>/docs/architecture); with a single module it can also be the output .json file",
        "help_no_codegraph": "do not read the codegraph index",
        "help_android_cli": "run `android describe` and attach its build metadata (runs Gradle, can be slow)",
        "help_no_timestamp": "do not write generated_at, so that regenerating without changes produces no diff "
                             "(alternatively, set SOURCE_DATE_EPOCH, which fixes the date)",
        "help_lang": "language of the messages and of the map legend; when absent, MODULE_MAP_LANG, "
                     "then `lang` in .module-map.toml, then es",
        "help_config": ".module-map.toml file; by default it is looked up from the module to the project root",
    },
}


def tr(lang, message, /, **values):
    return MESSAGES[lang][message].format(**values)


def bootstrap(lang):
    """Dependencies are missing: install them in a dedicated environment and re-run the script inside it."""
    if os.environ.get("MODULE_MAP_BOOTSTRAPPED"):
        sys.exit(tr(lang, "deps_unimportable", venv=VENV))
    if sys.version_info < (3, 10):
        sys.exit(tr(lang, "python_too_old"))
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = VENV / ".ready"  # records which versions were installed, so pip is not called on every run
    if not ready.is_file() or ready.read_text() != " ".join(REQUIREMENTS):
        print(tr(lang, "first_run", requirements=", ".join(REQUIREMENTS), venv=VENV), file=sys.stderr, flush=True)
        try:
            venv.create(VENV, with_pip=True, clear=True)
            subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                            *REQUIREMENTS], check=True)
        except (subprocess.CalledProcessError, OSError) as error:
            sys.exit(tr(lang, "venv_failed", error=error))
        ready.write_text(" ".join(REQUIREMENTS))
    env = {**os.environ, "MODULE_MAP_BOOTSTRAPPED": "1"}
    sys.exit(subprocess.run([str(python), __file__, *sys.argv[1:]], env=env).returncode)


def dependencies_available():
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_kotlin  # noqa: F401
    except ImportError:
        return False
    return True


PARSER = None  # built on first use, so importing this module needs neither tree-sitter nor network


def kotlin_parser():
    global PARSER
    if PARSER is None:
        import tree_sitter_kotlin
        from tree_sitter import Language, Parser
        PARSER = Parser(Language(tree_sitter_kotlin.language()))
    return PARSER


def toml_module():
    """tomllib (Python 3.11+) or its backport tomli; None when neither is available."""
    try:
        import tomllib
        return tomllib
    except ModuleNotFoundError:
        try:
            import tomli
            return tomli
        except ModuleNotFoundError:
            return None


TYPE_NODES = ("user_type", "nullable_type", "function_type", "parenthesized_type", "non_nullable_type")
VISIBILITY = {"public", "private", "internal", "protected"}
BUILD_FILES = ("build.gradle.kts", "build.gradle")
DOCS_DIR = Path("docs") / "architecture"  # default output, inside each module
DESCRIBE_TIMEOUT = 300  # seconds

# Architectural role (heuristic): first by annotation, then supertype, then name.
ROLE_BY_ANNOTATION = {"HiltViewModel": "viewmodel", "Dao": "dao", "Entity": "entity",
                      "Database": "database", "Module": "di_module",
                      "Composable": "composable", "AndroidEntryPoint": "android_entry",
                      "Provides": "di_module", "Binds": "di_module", "InstallIn": "di_module",
                      "TypeConverter": "type_converter", "Query": "dao",
                      "Insert": "dao", "Update": "dao", "Delete": "dao",
                      "SerializedName": "model", "Json": "model", "JsonClass": "model",
                      "Serializable": "model", "Parcelize": "model",
                      "BindingAdapter": "binding_adapter", "Preview": "composable"}
ROLE_BY_SUPERTYPE = {"ViewModel": "viewmodel", "Activity": "activity", "Fragment": "fragment",
                     "Service": "service", "BroadcastReceiver": "receiver", "ContentProvider": "provider",
                     "Worker": "worker", "Application": "application", "RoomDatabase": "database",
                     "ListAdapter": "adapter", "RecyclerView.Adapter": "adapter",
                     "PagingSource": "datasource", "RemoteMediator": "datasource",
                     "TypeConverter": "type_converter"}
ROLE_BY_NAME = {"Repository": "repository", "RepositoryImpl": "repository", "UseCase": "usecase",
                "Interactor": "usecase", "DataSource": "datasource",
                "Mapper": "mapper", "Converter": "mapper",
                "Router": "navigator", "Navigator": "navigator", "Coordinator": "navigator",
                "Adapter": "adapter", "ViewHolder": "viewholder",
                "State": "ui_state", "UiState": "ui_state", "UiEvent": "ui_event"}
HTTP_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "HTTP"}

# Layer-exclusive import prefixes: libraries that only one layer uses.
LAYER_BY_IMPORT = {
    "retrofit2.": "Data", "okhttp3.": "Data", "com.squareup.okhttp3.": "Data",
    "androidx.room.": "Data", "io.realm.": "Data",
    "app.cash.sqldelight.": "Data", "com.squareup.sqldelight.": "Data",
    "com.google.firebase.firestore.": "Data", "com.google.firebase.database.": "Data",
    "io.ktor.client.": "Data",
    "androidx.compose.": "Presentation", "android.view.": "Presentation",
    "android.widget.": "Presentation", "android.app.Activity": "Presentation",
    "androidx.fragment.": "Presentation", "androidx.recyclerview.": "Presentation",
    "androidx.navigation.": "Presentation", "androidx.viewpager": "Presentation",
    "dagger.Module": "DI", "dagger.Provides": "DI", "dagger.Binds": "DI",
    "dagger.hilt.": "DI", "dagger.multibindings.": "DI",
}


def layer_from_imports(imports):
    """Infer layer from file imports using library prefixes exclusive to one layer."""
    votes = {}
    for fqn in imports.values():
        for prefix, layer in LAYER_BY_IMPORT.items():
            if fqn.startswith(prefix):
                votes[layer] = votes.get(layer, 0) + 1
                break
    if not votes:
        return None
    best = max(votes, key=votes.get)
    total = sum(votes.values())
    if votes[best] / total >= 0.6:
        return best
    return None


# Travels inside the JSON so whoever consumes it does not have to guess the semantics.
LEGEND = {
    "es": {
        "edge_kinds": {
            "extends": "herencia de clase (o interfaz que extiende interfaz)",
            "implements": "implementación de interfaz",
            "depends_on": "colaborador recibido por constructor o campo @Inject",
            "provides": "módulo Hilt/Dagger que aporta ese tipo al grafo (@Provides / @Binds)",
            "uses_type": "el tipo aparece en propiedades o firmas",
            "calls": "llamada a función o método",
            "instantiates": "construcción de una instancia",
            "references": "otra referencia al símbolo",
        },
        "provenance": {
            "ast": "leído de la sintaxis del módulo",
            "codegraph": "resuelto por nombre; si aparece solo, nada en el archivo que llama nombra al destino "
                         "(mismo paquete o import con *): revisar si confidence es menor que 0.9",
            "codegraph + ast": "codegraph vio la referencia y el archivo que llama la respalda con un tipo "
                               "declarado o un import",
        },
        "details.resolved_by": "ast_receiver_type = el destino se fijó con el tipo declarado del receptor; "
                               "ast_import = se fijó con el import del nombre en el archivo que llama; "
                               "corrected_from es lo que proponía codegraph",
        "weight": "número de apariciones agregadas en la arista",
        "visibility": "si no aparece, es public",
        "external_nodes.origin": "project = otro módulo de este repo; library = dependencia externa",
        "module.dependencies.libraries.resolved": "coordenadas group:name:version leídas de "
                                                  "gradle/libs.versions.toml (una lista para un bundle)",
    },
    "en": {
        "edge_kinds": {
            "extends": "class inheritance (or an interface extending an interface)",
            "implements": "interface implementation",
            "depends_on": "collaborator received through the constructor or an @Inject field",
            "provides": "Hilt/Dagger module that contributes this type to the graph (@Provides / @Binds)",
            "uses_type": "the type appears in properties or signatures",
            "calls": "function or method call",
            "instantiates": "construction of an instance",
            "references": "any other reference to the symbol",
        },
        "provenance": {
            "ast": "read from the module's syntax",
            "codegraph": "resolved by name; when it appears alone, nothing in the calling file names the target "
                         "(same package or star import): review it when confidence is below 0.9",
            "codegraph + ast": "codegraph saw the reference and the calling file backs it with a declared type "
                               "or an import",
        },
        "details.resolved_by": "ast_receiver_type = the target was fixed with the declared type of the receiver; "
                               "ast_import = it was fixed with the import of the name in the calling file; "
                               "corrected_from is what codegraph proposed",
        "weight": "number of occurrences aggregated in the edge",
        "visibility": "public when absent",
        "external_nodes.origin": "project = another module of this repo; library = external dependency",
        "module.dependencies.libraries.resolved": "group:name:version coordinates read from "
                                                  "gradle/libs.versions.toml (a list for a bundle)",
    },
}

# codegraph node kinds that count as the "owner" of their members.
CG_TYPE_KINDS = {"class", "interface", "enum", "struct", "trait", "object"}


START = time.monotonic()
SYNCED = set()  # codegraph roots already synced in this run


def log(message):
    """Progress on stderr with the elapsed time, to show which step is running."""
    print(f"[module_map +{time.monotonic() - START:5.1f}s] {message}", file=sys.stderr, flush=True)


IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(\.\*)?(?:\s+as\s+(\w+))?\s*;?\s*$", re.M)


def related(a, b):
    """True if two FQNs name the same symbol or one is nested in the other (a class and its member)."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


# ---------- AST helpers ----------

def txt(node):
    return " ".join(node.text.decode().split()) if node else ""


def first(node, *types):
    return next((c for c in node.children if c.type in types), None)


def type_names(node):
    """All types mentioned in a type node, outermost first: Flow<List<User>> -> Flow, List, User."""
    if node is None or node.type == "annotation":  # `@Composable () -> Unit`: the annotation is not a type use
        return []
    names = []
    if node.type == "user_type":
        names.append(".".join(c.text.decode() for c in node.children if c.type == "identifier"))
    for c in node.children:
        names.extend(type_names(c))
    return names


def nominal(type_text):
    """'Foo<Bar>?' -> 'Foo'. None for lambdas and inferred types: the call target is unknown there."""
    m = re.fullmatch(r"([\w.]+)(<.*>)?\??", type_text or "")
    return m.group(1) if m else None


def read_modifiers(node):
    """-> (annotations, modifiers, visibility) of a declaration."""
    annotations, keywords = [], []
    mods = first(node, "modifiers")
    for m in mods.children if mods else []:
        (annotations if m.type == "annotation" else keywords).append(txt(m).lstrip("@"))
    visibility = next((k for k in keywords if k in VISIBILITY and k != "public"), None)  # None = public
    return annotations, [k for k in keywords if k not in VISIBILITY], visibility


def ann_names(annotations):
    """'field:Inject', 'POST("x")', 'dagger.Provides' -> {'Inject', 'POST', 'Provides'}"""
    return {m.group(1).rsplit(".", 1)[-1] for a in annotations if (m := re.match(r"(?:\w+:)?([\w.]+)", a))}


def kdoc(node):
    """KDoc right before the declaration, without the asterisks."""
    prev = node.prev_named_sibling
    if prev is None or prev.type != "block_comment" or not prev.text.startswith(b"/**"):
        return None
    lines = [re.sub(r"^\s*\* ?", "", line).strip() for line in prev.text.decode()[3:-2].splitlines()]
    return "\n".join(lines).strip() or None


def read_function(node):
    """-> (record, params [(name, types, type as text)], return types, receiver types)"""
    annotations, keywords, visibility = read_modifiers(node)
    params, returns, receiver, seen_params = [], [], [], False
    for c in node.children:
        if c.type == "function_value_parameters":
            seen_params = True
            for p in c.children:
                if p.type == "parameter":
                    declared = first(p, *TYPE_NODES)
                    params.append((txt(first(p, "identifier")), type_names(declared),
                                   txt(declared) if declared else None))
        elif c.type in TYPE_NODES:
            (returns if seen_params else receiver).extend(type_names(c))
    # Signature = from `fun` up to where the body starts.
    start = first(node, "fun").start_byte - node.start_byte
    body = first(node, "function_body")
    end = (body.start_byte if body else node.end_byte) - node.start_byte
    signature = " ".join(keywords + node.text[start:end].decode().split())
    record = {"name": txt(first(node, "identifier")), "signature": signature, "visibility": visibility,
              "annotations": annotations, "doc": kdoc(node)}
    return record, params, returns, receiver


def read_property(node):
    """-> (record, types). The type is None when Kotlin infers it: the AST cannot know it."""
    var = first(node, "variable_declaration")
    if var is None:  # destructuring: val (a, b) = ...
        return None, []
    annotations, keywords, visibility = read_modifiers(node)
    declared = first(var, *TYPE_NODES)
    record = {"name": txt(first(var, "identifier")), "type": txt(declared) if declared else None,
              "visibility": visibility, "modifiers": keywords, "annotations": annotations, "doc": kdoc(node)}
    if first(node, "var"):
        record["mutable"] = True
    return record, type_names(declared)


def role_of(node):
    """Role of a class or interface; functions can only be `composable` and are tagged when declared."""
    names = ann_names(node.get("annotations", []))
    for annotation, role in ROLE_BY_ANNOTATION.items():
        if annotation in names:
            return role
    for supertype in node.get("supertypes", []):
        base = supertype.split("<")[0].rsplit(".", 1)[-1]
        for suffix, role in ROLE_BY_SUPERTYPE.items():
            if base.endswith(suffix):
                return role
    if any(ann_names(f.get("annotations", [])) & HTTP_VERBS for f in node.get("functions", [])):
        return "api_service"
    for suffix, role in ROLE_BY_NAME.items():
        if node["name"].endswith(suffix):
            return role
    return None


# ---------- Gradle and Manifest ----------

def gradle_module(path, root):
    """(directory, Gradle path) of the module that contains `path`: the nearest ancestor with build.gradle(.kts)."""
    if root not in (path, *path.parents):
        return None, None
    for d in [path, *path.parents]:
        if any((d / f).is_file() for f in BUILD_FILES):
            return d, ":" + ":".join(d.relative_to(root).parts)
        if d == root:
            break
    return None, None


CONFIGURATION_RE = re.compile(
    r"(?:^|[{;])[ \t]*(\w*(?:[iI]mplementation|[aA]pi|[cC]ompileOnly|[rR]untimeOnly|[kK]sp|[kK]apt))\b[ \t]*", re.M)
# A notation the map can show as written: a string or an accessor (libs.x), optionally wrapped once: platform(...).
NOTATION_RE = re.compile(r"""(?:\w+\(\s*)?(?:"[^"]*"|'[^']*'|[\w.]+)\s*\)?""")
# group: 'g', name: 'a', version: 'v' in Groovy, group = "g", ... in the Kotlin DSL.
NAMED_COORDINATE_RE = re.compile(r"""\b(group|name|version)\s*[:=]\s*["']([^"']*)["']""")
PROJECT_RE = re.compile(r"""project\(\s*(?:path\s*[=:]\s*)?["'](:[^"']+)["']""")
VERSION_RANGE_RE = re.compile(r"[\[\]()+,]")


def strip_comments(text):
    """Gradle scripts without // and /* */ comments. A // right after a non-space is kept: it is a URL."""
    return re.sub(r"(^|\s)//[^\n]*", r"\1", re.sub(r"/\*.*?\*/", " ", text, flags=re.S), flags=re.M)


def closing_parenthesis(text, opening):
    """Index of the parenthesis that closes the one at text[opening], skipping strings; None if it never closes."""
    depth, quote, escaped = 0, None, False
    for i in range(opening, len(text)):
        c = text[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def dependency_argument(text, start):
    """What a configuration declares, read from the end of its name: what its parentheses enclose, or the rest of
    the line in Groovy without parentheses. A configuration block after it, { exclude(...) }, is left out."""
    if text.startswith("(", start):
        end = closing_parenthesis(text, start)
        if end is not None:
            return text[start + 1:end]
    line, quote = text[start:].split("\n", 1)[0], None
    for i, c in enumerate(line):
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'":
            quote = c
        elif c == "{":
            return line[:i]
    return line


def read_dependency(argument, configuration, catalog):
    """-> ("modules" | "libraries", entry), or None when the argument is no notation this reader knows."""
    value = " ".join(argument.split())
    project = PROJECT_RE.search(value)
    if project:
        return "modules", {"path": project.group(1), "configuration": configuration}
    if value.startswith("projects."):  # type-safe accessors: projects.core.model -> :core:model
        return "modules", {"path": ":" + value[len("projects."):].replace(".", ":"), "configuration": configuration}
    named = dict(NAMED_COORDINATE_RE.findall(value))
    if "group" in named and "name" in named:
        notation = ":".join(named[k] for k in ("group", "name", "version") if named.get(k))
    elif NOTATION_RE.fullmatch(value):
        notation = value.strip("\"'")
    else:
        return None
    library = {"notation": notation, "configuration": configuration}
    resolved = catalog_library(notation, catalog)
    if resolved:
        library["resolved"] = resolved
    return "libraries", library


def catalog_alias(alias):
    """Gradle turns the -, _ and . of a catalog alias into the dots of its accessor: compose-bom -> compose.bom."""
    return re.sub(r"[-_.]", ".", alias)


def read_version_catalog(root, lang):
    """-> (catalog, warning). gradle/libs.versions.toml, the default catalog, resolves the libs.* notations."""
    path = root / "gradle" / "libs.versions.toml"
    if not path.is_file():
        return None, None
    file = path.relative_to(root).as_posix()
    toml = toml_module()
    if toml is None:
        return None, tr(lang, "catalog_no_toml", file=file)
    try:
        data = toml.loads(path.read_text(encoding="utf-8"))
    except toml.TOMLDecodeError as error:
        return None, tr(lang, "catalog_invalid", file=file, error=error)

    def table(name):
        value = data.get(name, {})
        return value if isinstance(value, dict) else {}

    versions = table("versions")

    def version(spec, follow_ref=True):
        """'1.0', {ref = 'x'} or a rich version {strictly/require/prefer = '1.0'} -> '1.0'.

        In a rich version the first exact value wins, in the order strictly, require, prefer: a range
        ("[1.0, 2.0[", "1.+") says less than the prefer next to it. With only ranges, the first one as written.
        """
        if isinstance(spec, str):
            return spec
        if not isinstance(spec, dict):
            return None
        if follow_ref and "ref" in spec:
            return version(versions.get(spec["ref"]), follow_ref=False)
        values = [spec[k] for k in ("strictly", "require", "prefer") if isinstance(spec.get(k), str)]
        return next((v for v in values if not VERSION_RANGE_RE.search(v)), values[0] if values else None)

    libraries = {}
    for alias, spec in table("libraries").items():
        if isinstance(spec, str):  # "group:name:version"
            libraries[catalog_alias(alias)] = spec
        elif isinstance(spec, dict):
            module = spec.get("module") or (f"{spec['group']}:{spec['name']}" if "group" in spec and "name" in spec
                                            else None)
            if module:
                resolved = version(spec.get("version"))
                libraries[catalog_alias(alias)] = f"{module}:{resolved}" if resolved else module
    bundles = {catalog_alias(alias): [libraries[catalog_alias(m)] for m in members if catalog_alias(m) in libraries]
               for alias, members in table("bundles").items() if isinstance(members, list)}
    plugins = {}
    for alias, spec in table("plugins").items():
        plugin_id = spec.split(":")[0] if isinstance(spec, str) else spec.get("id") if isinstance(spec, dict) else None
        if plugin_id:
            plugins[catalog_alias(alias)] = plugin_id
    return {"libraries": libraries, "bundles": bundles, "plugins": plugins}, None


def catalog_library(notation, catalog):
    """libs.x -> 'group:name:version', libs.bundles.x -> list of them; None when the catalog does not have it."""
    inner = re.fullmatch(r"(?:\w+\()?([\w.]+)\)?", notation)  # platform(libs.compose.bom)
    accessor = inner.group(1) if inner else notation
    if not catalog or not accessor.startswith("libs."):
        return None
    key = accessor[len("libs."):]
    if key.startswith("bundles."):
        return catalog["bundles"].get(key[len("bundles."):])
    return catalog["libraries"].get(key)


def catalog_plugin(plugin, catalog):
    """libs.plugins.android.library -> com.android.library."""
    if catalog and plugin.startswith("libs.plugins."):
        return catalog["plugins"].get(plugin[len("libs.plugins."):])
    return None


def build_file(gradle_dir):
    return next((gradle_dir / f for f in BUILD_FILES if (gradle_dir / f).is_file()), None)


def read_gradle(gradle_dir, catalog=None, unparsed=None):
    """Plugins, namespace and dependencies read from build.gradle(.kts) without running Gradle.

    Declarations that look like a dependency but whose notation is not understood are appended to `unparsed`,
    as written, so that they become warnings instead of disappearing.
    """
    build = build_file(gradle_dir)
    if build is None:
        return {"type": None, "namespace": None, "plugins": [], "dependencies": {"modules": [], "libraries": []}}
    text = strip_comments(build.read_text(encoding="utf-8"))
    block = re.search(r"plugins\s*\{(.*?)\}", text, re.S)
    plugins = re.findall(r"""\b(?:id|alias|kotlin)\b\s*\(?\s*["']?([\w.\-]+)""", block.group(1)) if block else []
    joined = " ".join(catalog_plugin(p, catalog) or p for p in plugins).lower()
    module_type = next((f"android-{k}" for k in ("application", "dynamic-feature", "library")
                        if "android" in joined and k in joined), None)
    namespace = re.search(r"""namespace\s*=?\s*["']([^"']+)["']""", text)

    found = {"modules": [], "libraries": []}
    for match in CONFIGURATION_RE.finditer(text):
        argument = dependency_argument(text, match.end()).strip()
        if not argument:  # a block such as `kapt { ... }`, not a declaration
            continue
        dependency = read_dependency(argument, match.group(1), catalog)
        if dependency:
            found[dependency[0]].append(dependency[1])
        elif unparsed is not None:
            unparsed.append(f"{match.group(1)} {' '.join(argument.split())}"[:160])
    return {"type": module_type, "namespace": namespace.group(1) if namespace else None,
            "plugins": plugins, "dependencies": found}


def read_settings(root):
    settings = next((root / f for f in SETTINGS_FILES if (root / f).is_file()), None)
    if settings is None:
        return {}
    text = strip_comments(settings.read_text(encoding="utf-8"))
    name = re.search(r"""rootProject\.name\s*=\s*["']([^"']+)["']""", text)
    includes = " ".join(a or b for a, b in re.findall(r"\binclude\s*\(([^)]*)\)|\binclude\s+([^\n]+)", text))
    # Gradle also accepts include("app") without the leading colon.
    paths = re.findall(r"""["']([\w:.\-]+)["']""", includes)
    return {"name": name.group(1) if name else None,
            "modules": sorted({path if path.startswith(":") else ":" + path for path in paths})}


def read_manifest(path, namespace):
    android = "{http://schemas.android.com/apk/res/android}"
    root = ET.parse(path).getroot()
    package = namespace or root.get("package", "")

    def fqn(name):
        return package + name if name.startswith(".") else name if "." in name else f"{package}.{name}"

    components = []
    for el in root.iter():
        if el.tag in ("activity", "service", "receiver", "provider") and el.get(android + "name"):
            exported = el.get(android + "exported")
            components.append({"type": el.tag, "class": fqn(el.get(android + "name")),
                               "exported": None if exported is None else exported == "true"})
    return {"permissions": [el.get(android + "name") for el in root.iter("uses-permission")],
            "components": components}


# ---------- the map ----------

class ModuleMap:
    def __init__(self, root, module_dir, lang=DEFAULT_LANG):
        self.root = root
        self.module_dir = module_dir
        self.lang = lang
        self.nodes = {}      # id -> node declared in the module
        self.externals = {}  # id -> node from another module or a library
        self.edges = {}      # (from, to, kind) -> edge
        self.refs = []       # (from, kind, type name, file, detail) to be resolved in link()
        self.unresolved = []  # refs the AST could not place; codegraph retries them
        self.files = {}      # file -> {"package", "imports", "stars"}
        self.receivers = {}  # (node id, property or parameter) -> declared type, to validate calls
        self.warnings = []

    def rel(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def context(self, path):
        """Package and imports of a source file: from the AST for parsed files, read by regex for any other."""
        file = self.rel(path)
        if file not in self.files:
            text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            package = re.search(r"^\s*package\s+([\w.]+)", text, re.M)
            imports, stars = {}, []
            for fqn, star, alias in IMPORT_RE.findall(text):
                if star:
                    stars.append(fqn)
                else:
                    imports[alias or fqn.rsplit(".", 1)[-1]] = fqn
            self.files[file] = {"package": package.group(1) if package else "", "imports": imports, "stars": stars}
        return self.files[file]

    # --- step 1: AST ---

    def parse_file(self, path):
        tree = kotlin_parser().parse(path.read_bytes()).root_node
        file = self.rel(path)
        if tree.has_error:
            self.warnings.append(tr(self.lang, "partial_parse", file=file))
        header = first(tree, "package_header")
        package = txt(first(header, "qualified_identifier")) if header else ""
        imports, stars = {}, []
        for imp in tree.children:
            if imp.type != "import":
                continue
            fqn = txt(first(imp, "qualified_identifier"))
            if first(imp, "*"):
                stars.append(fqn)
            else:
                alias = first(imp, "identifier")
                imports[txt(alias) if alias else fqn.rsplit(".", 1)[-1]] = fqn
        self.files[file] = {"package": package, "imports": imports, "stars": stars}

        for n in tree.children:
            if n.type in ("class_declaration", "object_declaration"):
                self.declare_type(n, package, file, parent=None)
            elif n.type == "function_declaration":
                fn, params, returns, receiver = read_function(n)
                if not fn["name"]:  # no name: leftover of a parse error, already reported above
                    continue
                node = self.new_node(n, package, file, "function", **fn)
                node["role"] = "composable" if "Composable" in ann_names(fn["annotations"]) else None
                self.receivers.update({(node["id"], p): nominal(t) for p, _, t in params})
                self.use_types(node["id"], file, receiver + returns + [t for _, ts, _ in params for t in ts])
            elif n.type == "property_declaration":
                prop, types = read_property(n)
                if prop and prop["name"]:
                    node = self.new_node(n, package, file, "property", **prop)
                    self.use_types(node["id"], file, types)
            elif n.type == "type_alias":
                target = first(n, *TYPE_NODES)
                node = self.new_node(n, package, file, "typealias", name=txt(first(n, "identifier")),
                                     target=txt(target) if target else None, doc=kdoc(n))
                self.use_types(node["id"], file, type_names(target))

    def new_node(self, ts_node, scope, file, kind, name, **fields):
        node_id = f"{scope}.{name}" if scope else name
        if node_id in self.nodes:  # overloads or extensions with the same name
            node_id += f"@{ts_node.start_point[0] + 1}"
        suffix = 2
        while node_id in self.nodes:  # still collides (same line in different files, or generated code)
            node_id = f"{scope}.{name}@{ts_node.start_point[0] + 1}_{suffix}" if scope else \
                      f"{name}@{ts_node.start_point[0] + 1}_{suffix}"
            suffix += 1
        self.nodes[node_id] = {"id": node_id, "kind": kind, "name": name, "file": file,
                               "lines": [ts_node.start_point[0] + 1, ts_node.end_point[0] + 1], **fields}
        return self.nodes[node_id]

    def use_types(self, node_id, file, names):
        self.refs.extend((node_id, "uses_type", name, file, None) for name in names)

    def declare_type(self, n, scope, file, parent):
        annotations, keywords, visibility = read_modifiers(n)
        if n.type == "object_declaration":
            kind = "object"
        elif first(n, "interface"):
            kind = "interface"
        else:
            kind = next((k for k in ("enum", "annotation") if k in keywords), "class")
        name = txt(first(n, "identifier"))
        if not name:
            return
        node = self.new_node(n, scope, file, kind, name=name, visibility=visibility,
                             modifiers=keywords, annotations=annotations, doc=kdoc(n), parent=parent,
                             supertypes=[], constructor=[], properties=[], functions=[], entries=[])

        specifiers = first(n, "delegation_specifiers")
        for spec in specifiers.children if specifiers else []:
            if spec.type != "delegation_specifier":
                continue
            target = spec.children[0]  # constructor_invocation = class; user_type = interface
            supertype = target if target.type == "user_type" else first(target, "user_type")
            if supertype is None:
                continue
            base, *type_args = type_names(supertype)
            is_class = target.type == "constructor_invocation" or kind == "interface"
            node["supertypes"].append(txt(supertype))
            self.refs.append((node["id"], "extends" if is_class else "implements", base, file, None))
            self.use_types(node["id"], file, type_args)

        constructor = first(n, "primary_constructor")
        if constructor:
            constructor_annotations = read_modifiers(constructor)[0]
            node["constructor_annotations"] = constructor_annotations
            injected = "Inject" in ann_names(constructor_annotations)
            # In data/value/enum classes the parameters are data, not collaborators.
            holds_data = kind in ("enum", "annotation") or {"data", "value"} & set(keywords)
            parameters = first(constructor, "class_parameters")
            for p in parameters.children if parameters else []:
                if p.type != "class_parameter":
                    continue
                declared = first(p, *TYPE_NODES)
                param = txt(first(p, "identifier"))
                node["constructor"].append({"name": param, "type": txt(declared) if declared else None})
                self.receivers[(node["id"], param)] = nominal(node["constructor"][-1]["type"])
                detail = {"via": "constructor", "name": param}
                if injected:
                    detail["di"] = True
                for type_name in type_names(declared):
                    self.refs.append((node["id"], "uses_type" if holds_data else "depends_on", type_name, file,
                                      None if holds_data else detail))

        body = first(n, "class_body", "enum_class_body")
        if body:
            self.members(node, body, file)
        node["role"] = role_of(node)
        if not node["role"]:
            hint = layer_from_imports(self.files.get(file, {}).get("imports", {}))
            if hint:
                node["layer_hint"] = hint

    def members(self, node, body, file, static=False):
        """Members are stored inside their class node; nested types are nodes of their own."""
        for c in body.children:
            if c.type == "function_declaration":
                fn, params, returns, receiver = read_function(c)
                if not fn["name"]:
                    continue
                if static:
                    fn["static"] = True
                node["functions"].append(fn)
                self.use_types(node["id"], file, receiver + returns + [t for _, ts, _ in params for t in ts])
                binding = ann_names(fn["annotations"]) & {"Provides", "Binds"}
                if binding and returns:
                    self.refs.append((node["id"], "provides", returns[0], file,
                                      {"binding": binding.pop().lower(), "function": fn["name"]}))
            elif c.type == "property_declaration":
                prop, types = read_property(c)
                if not prop or not prop["name"]:
                    continue
                if static:
                    prop["static"] = True
                node["properties"].append(prop)
                self.receivers[(node["id"], prop["name"])] = nominal(prop["type"])
                if "Inject" in ann_names(prop["annotations"]):
                    detail = {"via": "field", "name": prop["name"], "di": True}
                    self.refs.extend((node["id"], "depends_on", t, file, detail) for t in types)
                else:
                    self.use_types(node["id"], file, types)
            elif c.type in ("class_declaration", "object_declaration"):
                self.declare_type(c, node["id"], file, parent=node["id"])
            elif c.type == "companion_object":
                inner = first(c, "class_body")
                if inner:
                    self.members(node, inner, file, static=True)
            elif c.type == "enum_entry":
                node["entries"].append(txt(first(c, "identifier")))

    # --- step 2: names -> ids ---

    def resolve(self, name, scope, file):
        """Type name as written -> id (FQN). None if it is a builtin, a generic, or cannot be determined."""
        ctx = self.files[file]
        head, _, rest = name.partition(".")
        while len(scope) > len(ctx["package"]):  # enclosing classes
            if f"{scope}.{name}" in self.nodes:
                return f"{scope}.{name}"
            scope = scope.rpartition(".")[0]
        if head in ctx["imports"]:
            return ctx["imports"][head] + (f".{rest}" if rest else "")
        for package in [ctx["package"], *ctx["stars"]]:  # same package, then star imports
            candidate = f"{package}.{name}" if package else name
            if candidate in self.nodes:
                return candidate
        if rest and head[0].islower():  # FQN written inline: com.acme.net.Api
            return name
        return None

    def known(self, node_id):
        """Make sure the id exists as a node; if it is not in the module, register it as external."""
        if node_id not in self.nodes:
            self.externals.setdefault(
                node_id, {"id": node_id, "name": node_id.rsplit(".", 1)[-1], "origin": "library"})
        return node_id

    def link(self):
        for src, kind, name, file, detail in self.refs:
            target = self.resolve(name, src, file)
            if target is None:
                self.unresolved.append((src, kind, name, file, detail))
            elif target != src:
                self.add_edge(src, self.known(target), kind, "ast", detail)

    def edge_list(self):
        """Final edges. uses_type is dropped when the same pair already has a stronger relationship."""
        stronger = ("extends", "implements", "depends_on", "provides")
        return [edge for (src, dst, kind), edge in self.edges.items()
                if kind != "uses_type" or not any((src, dst, k) in self.edges for k in stronger)]

    def add_edge(self, src, dst, kind, provenance, detail=None):
        edge = self.edges.setdefault((src, dst, kind), {"from": src, "to": dst, "kind": kind,
                                                        "provenance": [provenance], "weight": 0, "details": []})
        if edge["provenance"][0] == provenance:
            edge["weight"] += 1
        elif provenance not in edge["provenance"]:
            edge["provenance"].append(provenance)
        # Always record the detail regardless of provenance source, so that
        # AST details (DI info, constructor params) are kept when codegraph
        # created the edge first, and vice versa.
        if detail and detail not in edge["details"]:
            edge["details"].append(detail)
        return edge

    # --- step 3: codegraph ---

    def load_codegraph(self):
        db = next((d / ".codegraph" / "codegraph.db" for d in [self.module_dir, *self.module_dir.parents]
                   if (d / ".codegraph" / "codegraph.db").is_file()), None)
        if db is None:
            return {"status": "not_found", "hint": tr(self.lang, "codegraph_hint")}
        cg_root = db.parent.parent
        binary = shutil.which("codegraph")
        if cg_root in SYNCED:
            pass
        elif binary:  # the index must be up to date: edges are matched to the AST by line number
            log(tr(self.lang, "codegraph_sync"))
            subprocess.run([binary, "sync", "--quiet", str(cg_root)], capture_output=True)
            SYNCED.add(cg_root)
        else:
            self.warnings.append(tr(self.lang, "codegraph_not_in_path"))

        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        module_rel = self.module_dir.relative_to(cg_root).as_posix()
        prefix = "" if module_rel == "." else module_rel + "/"
        ranges = {}
        for node in self.nodes.values():
            ranges.setdefault(node["file"], []).append((*node["lines"], node["id"]))

        def identify(node_id, kind, name, qualified_name, path, line):
            """-> (map id that owns a codegraph symbol, info to register it, or None if the map already has it).

            Members are lifted to their class.
            """
            inside = [(end - start, nid) for start, end, nid in ranges.get(self.rel(cg_root / path), [])
                      if start <= line <= end]
            if inside:
                return min(inside)[1], None
            if kind not in CG_TYPE_KINDS:
                parent = con.execute(
                    "SELECT n.kind, n.name, n.qualified_name FROM edges e JOIN nodes n ON n.id = e.source "
                    "WHERE e.target = ? AND e.kind = 'contains'", (node_id,)).fetchone()
                if parent and parent[0] in CG_TYPE_KINDS:
                    kind, name, qualified_name = parent
            if kind in CG_TYPE_KINDS:
                owner_id = qualified_name.replace("::", ".")
            else:  # top-level or extension function: codegraph does not qualify these with their package
                package = self.context(cg_root / path)["package"]
                owner_id = f"{package}.{name}" if package else name
            if owner_id in self.nodes:
                return owner_id, None
            return owner_id, {"id": owner_id, "kind": kind, "name": name, "file": self.rel(cg_root / path)}

        def owner(*row):
            """Map id for a codegraph symbol, registering it as a node or an external if it is new."""
            owner_id, info = identify(*row)
            if info and row[4].startswith(prefix):  # inside the module but without AST (e.g. a .java file)
                self.nodes[owner_id] = {**info, "source": "codegraph"}
            elif info:
                module = gradle_module((cg_root / row[4]).parent, self.root)[1]
                self.externals.setdefault(owner_id, {}).update({**info, "origin": "project", "module": module})
            return owner_id

        located = {}

        def locate(fqn):
            """Map id for an FQN taken from an import: in the module, in another module of the repo, or a library."""
            if fqn in self.nodes:
                return fqn
            if fqn not in located:
                located[fqn] = None
                candidates = con.execute(
                    "SELECT id, kind, name, qualified_name, file_path, start_line FROM nodes "
                    "WHERE name = ? AND kind NOT IN ('import', 'namespace', 'file')", (fqn.rpartition(".")[2],))
                for row in candidates.fetchall():
                    if not in_test_source_set(row[4]) and identify(*row)[0] == fqn:
                        located[fqn] = owner(*row)
                        break
            return located[fqn] or self.known(fqn)

        def package_of(node_id):
            node = self.nodes.get(node_id) or self.externals.get(node_id) or {}
            return self.context(self.root / node["file"])["package"] if "file" in node else node_id.rpartition(".")[0]

        # Edges synthesized by the dynamic-dispatch heuristic (interface -> impl) are skipped:
        # the AST already gives `implements`, and as `calls` they would confuse the diagram.
        rows = con.execute(
            """SELECT e.kind, e.line, e.metadata,
                      s.id, s.kind, s.name, s.qualified_name, s.file_path, s.start_line,
                      t.id, t.kind, t.name, t.qualified_name, t.file_path, t.start_line
               FROM edges e JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target
               WHERE e.kind NOT IN ('contains', 'imports') AND COALESCE(e.provenance, '') != 'heuristic'
                 AND s.kind NOT IN ('file', 'namespace', 'import') AND t.kind NOT IN ('file', 'namespace', 'import')
                 AND (substr(s.file_path, 1, :n) = :p OR substr(t.file_path, 1, :n) = :p)""",
            {"n": len(prefix), "p": prefix}).fetchall()
        used = discarded = 0
        for kind, line, metadata, *ends in rows:
            if in_test_source_set(ends[4]) or in_test_source_set(ends[10]):
                continue  # the map describes production code, same as the AST
            src, dst = owner(*ends[:6]), owner(*ends[6:])
            metadata = json.loads(metadata or "{}")
            detail = {"from": ends[2], "to": ends[8], "line": line,
                      "confidence": metadata.get("confidence"), "resolved_by": metadata.get("resolvedBy")}
            # codegraph resolves by name, so its target can be a namesake in an unrelated module.
            # The calling file settles it, in this order: the declared type of the receiver
            # (`repo.load()`, or `useCase()` via operator invoke), the import of the referenced name,
            # an import of the target itself, or the target being visible without an import.
            # An edge with none of these has no support and is discarded.
            ref = metadata.get("refName", "")
            head, last = ref.split(".")[0], ref.rsplit(".", 1)[-1]
            ctx = self.context(cg_root / ends[4])
            declared = self.receivers.get((src, head)) if kind == "calls" else None
            typed = self.resolve(declared, src, self.nodes[src]["file"]) if declared else None
            imported = ctx["imports"].get(head) or ctx["imports"].get(last)
            confirmed = True
            if typed:
                detail.update(confidence=None, resolved_by="ast_receiver_type")
                if typed != dst:
                    detail.update(to=ref.split(".", 1)[1] if "." in ref else "invoke", corrected_from=dst)
                    dst = self.known(typed)
            elif imported and not related(imported, dst):
                detail.update(confidence=None, resolved_by="ast_import", to=last, corrected_from=dst)
                dst = locate(imported)
            elif any(related(fqn, dst) for fqn in ctx["imports"].values()):
                pass
            elif (package_of(dst) in [ctx["package"], *ctx["stars"]]
                  or any(dst.startswith(star + ".") for star in ctx["stars"])):
                confirmed = False  # same package or star import: plausible, but nothing names the target
            else:
                discarded += 1
                continue
            if src == dst or (src not in self.nodes and dst not in self.nodes):
                continue
            edge = self.add_edge(src, dst, kind, "codegraph", detail)
            if confirmed and "ast" not in edge["provenance"]:
                edge["provenance"].append("ast")
            used += 1

        # Types the AST could not place (Java classes in the same package, star imports): look them up in the index.
        found = {}
        for src, kind, name, file, detail in self.unresolved:
            for package in [self.files[file]["package"], *self.files[file]["stars"]]:
                qualified_name = f"{package}::{name.replace('.', '::')}"
                if qualified_name not in found:
                    found[qualified_name] = con.execute(
                        "SELECT id, kind, name, qualified_name, file_path, start_line FROM nodes "
                        "WHERE qualified_name = ? AND kind NOT IN ('import', 'namespace', 'file')",
                        (qualified_name,)).fetchone()
                if found[qualified_name] and not in_test_source_set(found[qualified_name][4]):
                    dst = owner(*found[qualified_name])
                    if dst != src:
                        self.add_edge(src, dst, kind, "ast", detail)
                    break

        # Externals the AST saw through an import: if codegraph knows them, they belong to another module of the repo.
        for external in self.externals.values():
            if external["origin"] == "project":
                continue
            parts = external["id"].split(".")
            cut = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts) - 1)
            qualified_name = ".".join(parts[:cut]) + "::" + "::".join(parts[cut:])
            row = con.execute("SELECT kind, file_path FROM nodes WHERE qualified_name = ? "
                              "AND kind NOT IN ('import', 'namespace', 'file')", (qualified_name,)).fetchone()
            if row and not in_test_source_set(row[1]):
                external.update(kind=row[0], file=self.rel(cg_root / row[1]), origin="project",
                                module=gradle_module((cg_root / row[1]).parent, self.root)[1])
        try:
            version = con.execute("SELECT value FROM project_metadata WHERE key = 'indexed_with_version'").fetchone()
        except sqlite3.OperationalError:
            version = None
        con.close()
        return {"status": "ok", "db": self.rel(db), "version": version[0] if version else None,
                "edges_used": used, "edges_discarded": discarded}


# ---------- Android CLI ----------

def android_describe(root, lang=DEFAULT_LANG):
    """`android describe` prints paths to JSON files with the project's build structure; they are attached as is.

    The output format is taken from the Android CLI documentation, not verified against the binary:
    so when no JSON paths show up, the raw output is kept.
    """
    binary = shutil.which("android")
    if not binary:
        return {"status": "not_found"}
    # Triggers Gradle and can take minutes. The limit stays below the one agents usually put on a
    # command, so the script finishes on its own and the map is still written.
    log(tr(lang, "describe_running", minutes=DESCRIBE_TIMEOUT // 60))
    try:
        run = subprocess.run([binary, "describe", f"--project_dir={root}"],
                             capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(tr(lang, "describe_timeout"))
        return {"status": "timeout"}
    if run.returncode != 0:
        return {"status": "error", "message": (run.stderr or run.stdout).strip()[-1000:]}
    describe = {}
    for token in re.findall(r"\S+\.json", run.stdout):
        if Path(token).is_file():
            describe[token] = json.loads(Path(token).read_text(encoding="utf-8"))
    if not describe:  # output format differs from the expected one: nothing is lost, it is kept raw
        return {"status": "ok", "raw_output": run.stdout.strip()[-4000:]}
    return {"status": "ok", "describe": describe}


# ---------- output ----------

def in_test_source_set(path):
    """True if the path goes through a test source set: src/test, src/androidTest, src/testDebug..."""
    parts = Path(path).parts
    return any(a == "src" and "test" in b.lower() for a, b in zip(parts, parts[1:]))


def kotlin_files(module_dir, gradle_dir, root):
    """.kt sources of the module: no build/, no test source sets and no nested submodules."""
    cache = {}  # directory -> gradle_module result, avoids re-walking the tree for sibling files
    for path in sorted(module_dir.rglob("*.kt")):
        relative = path.relative_to(gradle_dir or module_dir)
        if relative.parts[:1] == ("build",) or in_test_source_set(relative):
            continue
        parent = path.parent
        if parent not in cache:
            cache[parent] = gradle_module(parent, root)[0]
        if cache[parent] == gradle_dir:
            yield path


def compact(value):
    """Drop empty keys: fewer tokens for the LLM that reads the JSON."""
    if isinstance(value, dict):
        cleaned = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, [], {})}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def dump(result):
    """One node or edge per line: readable, diffable, and far fewer tokens than indented JSON."""
    parts = []
    for key, value in result.items():
        if key in ("nodes", "external_nodes", "edges"):
            rows = ",\n".join("  " + json.dumps(row, ensure_ascii=False) for row in value)
            parts.append(f'"{key}": [\n{rows}\n]')
        else:
            parts.append(f'"{key}": {json.dumps(value, ensure_ascii=False)}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def inner_modules(directory):
    """Gradle modules in a directory (itself included), without entering build/, buildSrc or hidden directories."""
    found = []
    for current, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in ("build", "buildSrc") and not d.startswith("."))
        if any(f in files for f in BUILD_FILES):
            found.append(Path(current))
    return found


def generation_time():
    """Now, or SOURCE_DATE_EPOCH when it is set (the convention of reproducible builds)."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    moment = datetime.fromtimestamp(int(epoch), timezone.utc) if epoch.isdigit() else datetime.now(timezone.utc)
    return moment.isoformat(timespec="seconds")


def map_module(module_dir, root, use_codegraph, android_cli, lang=DEFAULT_LANG, catalog=None, warnings=(),
               timestamp=True):
    """-> (name for the file, map, summary), or None if the module has no Kotlin sources."""
    gradle_dir, gradle_path = gradle_module(module_dir, root)
    mm = ModuleMap(root, module_dir, lang)
    mm.warnings.extend(warnings)
    files = list(kotlin_files(module_dir, gradle_dir, root))
    if not files:
        return None
    log(tr(lang, "parsing", module=gradle_path or module_dir.name, count=len(files)))
    for path in files:
        mm.parse_file(path)
    mm.link()

    module = {"path": gradle_path, "dir": mm.rel(module_dir)}
    if gradle_dir:
        unparsed = []
        module.update(read_gradle(gradle_dir, catalog, unparsed))
        mm.warnings.extend(tr(lang, "dependency_unparsed", file=mm.rel(build_file(gradle_dir)), declaration=d)
                           for d in unparsed)
        manifest = gradle_dir / "src" / "main" / "AndroidManifest.xml"
        if manifest.is_file():
            try:
                module["manifest"] = read_manifest(manifest, module["namespace"])
            except ET.ParseError as error:
                mm.warnings.append(tr(lang, "manifest_invalid", file=mm.rel(manifest), error=error))

    sources = {"ast": {"parser": "tree-sitter-kotlin", "files": len(files)},
               "codegraph": mm.load_codegraph() if use_codegraph else {"status": "skipped"},
               "android_cli": android_cli}
    edges = mm.edge_list()
    linked = {end for edge in edges for end in (edge["from"], edge["to"])}
    externals = [n for n in mm.externals.values() if n["id"] in linked]  # no orphan externals
    # No absolute root and an optional timestamp: the map is meant to be committed, and regenerating it
    # without code changes, on any machine, must not produce a diff.
    result = compact({
        "schema": "module-map/1",
        "generated_at": generation_time() if timestamp else None,
        "legend": LEGEND[lang],
        "project": read_settings(root),
        "module": module,
        "sources": sources,
        "nodes": list(mm.nodes.values()),
        "external_nodes": externals,
        "edges": edges,
        "warnings": mm.warnings,
    })
    name = (gradle_path or "").strip(":").replace(":", "-") or module_dir.name
    summary = tr(lang, "summary", nodes=len(mm.nodes), externals=len(externals), edges=len(edges),
                 warnings=len(mm.warnings))
    return name, result, summary


def find_config(start, root):
    """.module-map.toml in `start` or one of its parents, up to the project root."""
    for directory in [start, *start.parents]:
        if (directory / CONFIG_FILE).is_file():
            return directory / CONFIG_FILE
        if directory == root:
            break
    return None


def read_config(path, lang):
    toml = toml_module()
    if toml is None:
        sys.exit(tr(lang, "config_no_toml", file=path))
    try:
        return toml.loads(path.read_text(encoding="utf-8"))
    except toml.TOMLDecodeError as error:
        sys.exit(tr(lang, "config_invalid_toml", file=path, error=error))


def requested_lang(flag):
    """Language chosen before the config is read: --lang, then MODULE_MAP_LANG."""
    lang = flag or os.environ.get("MODULE_MAP_LANG")
    if lang and lang not in LANGS:
        sys.exit(tr(DEFAULT_LANG, "bad_lang", value=lang, langs=", ".join(LANGS)))
    return lang


def parse_args():
    # --lang is read first so that --help comes out in the requested language.
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--lang", choices=LANGS)
    lang = requested_lang(early.parse_known_args()[0].lang) or DEFAULT_LANG
    cli = argparse.ArgumentParser(description=tr(lang, "cli_description"))
    cli.add_argument("module_dir", type=Path, help=tr(lang, "help_module_dir"))
    cli.add_argument("-o", "--output", type=Path, help=tr(lang, "help_output"))
    cli.add_argument("--no-codegraph", action="store_true", help=tr(lang, "help_no_codegraph"))
    cli.add_argument("--android-cli", action="store_true", help=tr(lang, "help_android_cli"))
    cli.add_argument("--no-timestamp", action="store_true", help=tr(lang, "help_no_timestamp"))
    cli.add_argument("--no-android-cli", action="store_true", help=argparse.SUPPRESS)  # former default, now a no-op
    cli.add_argument("--lang", choices=LANGS, help=tr(lang, "help_lang"))
    cli.add_argument("--config", type=Path, help=tr(lang, "help_config"))
    return cli.parse_args()


def main():
    args = parse_args()
    lang = requested_lang(args.lang) or DEFAULT_LANG
    if not dependencies_available():
        bootstrap(lang)

    module_dir = args.module_dir.resolve()
    if not module_dir.is_dir():
        sys.exit(tr(lang, "no_dir", path=module_dir))
    root = next((d for d in [module_dir, *module_dir.parents]
                 if any((d / f).is_file() for f in SETTINGS_FILES)), module_dir)
    if args.config and not args.config.is_file():
        sys.exit(tr(lang, "config_not_found", path=args.config))
    config_path = args.config or find_config(module_dir, root)
    config = read_config(config_path, lang) if config_path else {}
    lang = requested_lang(args.lang) or config.get("lang") or DEFAULT_LANG
    if lang not in LANGS:
        sys.exit(tr(DEFAULT_LANG, "bad_lang", value=lang, langs=", ".join(LANGS)))

    catalog, catalog_warning = read_version_catalog(root, lang)
    warnings = [catalog_warning] if catalog_warning else []
    android_cli = android_describe(root, lang) if args.android_cli else {"status": "skipped"}

    def run(directory):
        return map_module(directory, root, not args.no_codegraph, android_cli, lang, catalog, warnings,
                          timestamp=not args.no_timestamp)

    modules = inner_modules(module_dir)
    if modules in ([], [module_dir]):  # a single module, or a package inside a module
        mapped = run(module_dir)
        if mapped is None:
            sys.exit(tr(lang, "no_kotlin", path=module_dir))
        name, result, summary = mapped
        output = args.output or module_dir / DOCS_DIR
        if output.suffix != ".json":  # a directory: same layout as with several modules
            output = output / f"{name}.module-map.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(dump(result), encoding="utf-8")
        log(f"{output}: {summary}")
        return

    if args.output and args.output.suffix == ".json":
        sys.exit(tr(lang, "output_not_json", path=module_dir, count=len(modules)))
    log(tr(lang, "modules_found", path=module_dir, count=len(modules)))
    written = 0
    for directory in modules:
        mapped = run(directory)
        if mapped is None:
            log(tr(lang, "module_skipped", path=directory.relative_to(root).as_posix() or "."))
            continue
        name, result, summary = mapped
        output = (args.output or directory / DOCS_DIR) / f"{name}.module-map.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(dump(result), encoding="utf-8")
        log(f"{output}: {summary}")
        written += 1
    if not written:
        sys.exit(tr(lang, "no_module_has_kotlin", path=module_dir))
    log(tr(lang, "maps_written_to", count=written, path=args.output) if args.output
        else tr(lang, "maps_written_each", count=written))


if __name__ == "__main__":
    main()
