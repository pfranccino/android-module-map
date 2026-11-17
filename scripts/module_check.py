#!/usr/bin/env python3
"""Check module maps against the layer rules, for pre-commit, CI and coding agents.

    python3 scripts/module_check.py <directory> --generate   # build the maps in a temporary folder, check, discard them
    python3 scripts/module_check.py <directory>              # check the maps module_map.py already wrote

Options: --json, --config FILE, --lang es|en, --skip-freshness.

Running it is always a choice: nothing installs it in a pre-commit hook or a CI job. With --generate it
writes nothing into the project: the maps go to a temporary folder and codegraph is not run (its index
lives in the project), so call edges are not checked in that mode.

What it reports, per module:

- layer violations: an edge from a class of the module to a class whose layer forms a forbidden pair
  ([layers].forbidden), inside the module or in another module of the project. A type of another module
  is recognized by codegraph, or by the maps of the other modules, which are read too;
- forbidden library dependencies: an edge from a layer to a library type whose name starts with a
  prefix listed for that layer in [check].forbidden_external (by default, Domain may not use Android,
  serialization, network or Hilt module types);
- forbidden annotations: an annotation on a class, function or property of a layer whose simple name is
  listed for that layer in [check].forbidden_annotations (by default, Room, serialization and Hilt
  annotations in Domain). Maps keep annotations by simple name, so this is a name check;
- advisory edges: violations backed only by codegraph with a confidence under 0.9. They are listed apart,
  never counted as violations, and never used to infer a class's layer;
- coverage: stale maps, maps whose module folder cannot be found, Kotlin modules without a map, maps with
  partial parses, types that look like the project's but that no map or codegraph resolves, classes no
  layer rule classifies, and maps built without codegraph.

Exit codes: 0 when everything was checked and nothing is violated; 1 when there are violations; 2 when
the check is incomplete (see coverage above; unclassified classes count only with
[check].fail_on_unchecked = true, unresolved project types unless [check].fail_on_unresolved = false),
or when the configuration, the arguments or a map are invalid. Exit 2 wins over 1: violations read from
an incomplete check may not be all, or the current, ones.

No model and no third-party packages: the same maps always give the same result.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_diagrams as md  # noqa: E402
import module_map as mapper  # noqa: E402  (imported for its module discovery and messages; no parsing here)

SCHEMA_VERSION = 1
DEFAULT_FORBIDDEN_EXTERNAL = {
    "Domain": [
        "android.", "androidx.", "kotlinx.serialization.", "retrofit2.", "okhttp3.", "io.ktor.",
        "com.squareup.moshi.", "com.google.gson.", "dagger.Module", "dagger.Provides", "dagger.Binds",
        "dagger.hilt.",
    ],
}
DEFAULT_FORBIDDEN_ANNOTATIONS = {
    "Domain": [
        "Entity", "Dao", "Database", "TypeConverter", "TypeConverters", "Embedded", "Relation", "PrimaryKey",
        "ColumnInfo", "Serializable", "SerialName", "JsonClass", "SerializedName", "Module", "Provides", "Binds",
        "InstallIn", "HiltViewModel", "AndroidEntryPoint", "HiltAndroidApp",
    ],
}
CHECK_KEYS = ("forbidden_external", "forbidden_annotations", "fail_on_unchecked", "fail_on_unresolved")
CONFIDENCE_FLOOR = 0.9
SOURCE_SUFFIXES = {".kt", ".kts", ".java", ".xml", ".gradle", ".toml"}
SKIP_DIRS = {"build", ".gradle", ".idea", ".git", ".codegraph"}
GENERATED_NAMES = ("R", "BuildConfig")
GENERATED_PREFIXES = ("Hilt_", "Dagger")
GENERATED_SUFFIXES = ("_Factory", "_Impl", "_MembersInjector", "_HiltModules", "_GeneratedInjector", "Directions", "Args")

MESSAGES = {
    "en": {
        "status_pass": "No violations in {maps} maps.",
        "status_violations": "{count} violations in {maps} maps.",
        "status_incomplete": "The check is incomplete. Exit code 2.",
        "violation_layer": "{source} -> {target}: {pair} ({edge}) at {where}",
        "violation_external": "{source} -> {target}: {layer} may not use {prefix}* ({edge}) at {where}",
        "violation_annotation": "{source}: {layer} may not use @{annotation} at {where}",
        "advisory": "possible violation backed only by codegraph, confidence {confidence}: {source} -> {target} ({edge}) at {where}",
        "stale": "stale map: {source} is newer than the map. Run module_map.py again, or use --generate.",
        "module_not_found": "the module folder ({dir}) was not found, so freshness was not checked.",
        "missing_maps": "Kotlin modules without a map, not checked: {modules}",
        "partial_parse": "partial parse, declarations may be missing: {files}",
        "unresolved": "types that look like the project's but no map or codegraph resolves, not checked: {names}",
        "unchecked": "{count} classes have no layer, so no layer rule checks them: {names}",
        "no_codegraph": "built without codegraph: call edges are missing, so violations through calls are not checked.",
        "no_maps": "No module maps under {path}. Run module_map.py on it first, or use --generate.",
        "map_error": "{path}: the map cannot be read: {error}",
        "invalid": "{file}: [check] {key} is invalid: {hint}",
        "unknown_key": "{file}: unknown key `check.{key}`",
        "hint_table": "a table of layer = [strings], with layers from {layers}",
        "hint_bool": "true or false",
        "help": "Check module maps against the layer rules. Exit 0: pass; 1: violations; 2: incomplete or error.",
        "help_path": "a map, a folder of maps, or a folder with modules (their docs/architecture maps are used)",
        "help_json": "print the result as JSON",
        "help_skip_freshness": "do not compare the maps with the source files (use only when the maps were just generated)",
        "help_generate": "build the maps of the whole project in a temporary folder with module_map.py, without codegraph, check them and discard them; nothing is written into the project",
        "generate_needs_dir": "--generate needs a module or project folder, not {path}",
        "generate_failed": "module_map.py could not build the maps: {error}",
        "generated": "generated for this check",
    },
    "es": {
        "status_pass": "Sin violaciones en {maps} mapas.",
        "status_violations": "{count} violaciones en {maps} mapas.",
        "status_incomplete": "La revisión quedó incompleta. Código de salida 2.",
        "violation_layer": "{source} -> {target}: {pair} ({edge}) en {where}",
        "violation_external": "{source} -> {target}: {layer} no puede usar {prefix}* ({edge}) en {where}",
        "violation_annotation": "{source}: {layer} no puede usar @{annotation} en {where}",
        "advisory": "posible violación respaldada solo por codegraph, confianza {confidence}: {source} -> {target} ({edge}) en {where}",
        "stale": "mapa desactualizado: {source} es más nuevo que el mapa. Corre module_map.py otra vez, o usa --generate.",
        "module_not_found": "no se encontró la carpeta del módulo ({dir}), así que no se revisó si está al día.",
        "missing_maps": "módulos Kotlin sin mapa, sin revisar: {modules}",
        "partial_parse": "parse parcial, pueden faltar declaraciones: {files}",
        "unresolved": "tipos que parecen del proyecto pero que ningún mapa ni codegraph resuelve, sin revisar: {names}",
        "unchecked": "{count} clases no tienen capa, así que ninguna regla de capas las revisa: {names}",
        "no_codegraph": "generado sin codegraph: faltan las aristas de llamadas, así que no se revisan violaciones a través de llamadas.",
        "no_maps": "No hay mapas de módulos bajo {path}. Corre module_map.py sobre esa ruta primero, o usa --generate.",
        "map_error": "{path}: no se puede leer el mapa: {error}",
        "invalid": "{file}: [check] {key} no es válido: {hint}",
        "unknown_key": "{file}: clave desconocida `check.{key}`",
        "hint_table": "una tabla capa = [textos], con capas de {layers}",
        "hint_bool": "true o false",
        "help": "Revisa los mapas de módulos contra las reglas de capas. Salida 0: pasa; 1: violaciones; 2: incompleta o error.",
        "help_path": "un mapa, una carpeta de mapas, o una carpeta con módulos (se usan sus mapas en docs/architecture)",
        "help_json": "imprime el resultado como JSON",
        "help_skip_freshness": "no compara los mapas con los fuentes (úsalo solo cuando los mapas se acaban de generar)",
        "help_generate": "genera los mapas de todo el proyecto en una carpeta temporal con module_map.py, sin codegraph, los revisa y los descarta; no escribe nada en el proyecto",
        "generate_needs_dir": "--generate necesita la carpeta de un módulo o del proyecto, no {path}",
        "generate_failed": "module_map.py no pudo generar los mapas: {error}",
        "generated": "generado para esta revisión",
    },
}
PARTIAL_PARSE_PREFIXES = tuple(
    mapper.MESSAGES[lang]["partial_parse"].split("{file}")[0] for lang in mapper.MESSAGES)


def t(lang, message, /, **values):
    return MESSAGES[lang][message].format(**values)


class Options:
    def __init__(self, config, path, lang):
        section = config.get("check", {})
        hint = t(lang, "hint_table", layers=", ".join(md.LAYERS))
        if not isinstance(section, dict):
            sys.exit(t(lang, "invalid", file=path, key="", hint=hint))
        for key in section:
            if key not in CHECK_KEYS:
                sys.exit(t(lang, "unknown_key", file=path, key=key))
        self.forbidden_external = self.table(section, "forbidden_external", DEFAULT_FORBIDDEN_EXTERNAL, path, lang, hint)
        self.forbidden_annotations = self.table(section, "forbidden_annotations", DEFAULT_FORBIDDEN_ANNOTATIONS, path, lang, hint)
        self.fail_on_unchecked = self.flag(section, "fail_on_unchecked", False, path, lang)
        self.fail_on_unresolved = self.flag(section, "fail_on_unresolved", True, path, lang)

    @staticmethod
    def table(section, key, default, path, lang, hint):
        value = section.get(key, default)
        valid = isinstance(value, dict) and all(
            layer in md.LAYERS and isinstance(items, list) and all(isinstance(i, str) and i for i in items)
            for layer, items in value.items())
        if not valid:
            sys.exit(t(lang, "invalid", file=path, key=key, hint=hint))
        return value

    @staticmethod
    def flag(section, key, default, path, lang):
        value = section.get(key, default)
        if not isinstance(value, bool):
            sys.exit(t(lang, "invalid", file=path, key=key, hint=t(lang, "hint_bool")))
        return value


def low_confidence(edge):
    """The lowest codegraph confidence of an edge backed only by codegraph, or None."""
    if edge.get("provenance") != ["codegraph"]:
        return None
    values = [d["confidence"] for d in edge.get("details", []) if "confidence" in d]
    if values and min(values) < CONFIDENCE_FLOOR:
        return min(values)
    return None


def build_map(data, rules, lang):
    """A module_diagrams Map whose inferred layers ignore low-confidence codegraph edges."""
    m = md.Map(data, rules, lang)
    m.layers = {node_id: m.layer(node) for node_id, node in m.nodes.items()}
    all_edges = m.edges
    m.edges = [e for e in all_edges if low_confidence(e) is None]
    m._propagate_layers()
    m.edges = all_edges
    return m


def annotation_name(text):
    return text.split("(", 1)[0].strip().lstrip("@").rsplit(".", 1)[-1]


def looks_generated(type_id):
    name = type_id.rsplit(".", 1)[-1]
    parts = type_id.split(".")
    return (name in GENERATED_NAMES or "R" in parts[:-1] or name.startswith(GENERATED_PREFIXES)
            or name.endswith(GENERATED_SUFFIXES))


def find_violations(m, index, prefixes, options):
    violations, advisory, unresolved = [], [], set()
    for edge in m.edges:
        source = edge["from"]
        if source not in m.nodes:
            continue
        source_layer = m.layers[source]
        target = edge["to"]
        record = None
        if target in m.nodes:
            if (source_layer, m.layers[target]) in m.rules.forbidden:
                record = {"rule": "layer", "scope": "module", "layers": [source_layer, m.layers[target]]}
        elif target in m.externals:
            external = m.externals[target]
            if target in index:
                target_module, target_layer = index[target]
                if (source_layer, target_layer) in m.rules.forbidden:
                    record = {"rule": "layer", "scope": "project", "layers": [source_layer, target_layer],
                              "target_module": target_module}
            elif external.get("origin") == "project":
                target_layer = m.layer(external)
                if (source_layer, target_layer) in m.rules.forbidden:
                    record = {"rule": "layer", "scope": "project", "layers": [source_layer, target_layer],
                              "target_module": external.get("module")}
            else:
                prefix = next((p for p in options.forbidden_external.get(source_layer, []) if target.startswith(p)), None)
                if prefix:
                    record = {"rule": "external", "scope": "library", "layer": source_layer, "prefix": prefix}
                elif target.startswith(prefixes) and not looks_generated(target):
                    unresolved.add(target)
        if record is None:
            continue
        record.update({
            "from": m.label(source), "from_id": source,
            "to": m.label(target) if target in m.nodes else target, "to_id": target,
            "edge": edge["kind"], "where": m.where(edge), "provenance": edge.get("provenance", []),
        })
        confidence = low_confidence(edge)
        if confidence is not None:
            record["confidence"] = confidence
            advisory.append(record)
        else:
            violations.append(record)
    for node_id, node in m.nodes.items():
        layer = m.layers[node_id]
        forbidden = set(options.forbidden_annotations.get(layer, []))
        if not forbidden:
            continue
        members = [node] + node.get("functions", []) + node.get("properties", [])
        for member in members:
            for annotation in member.get("annotations", []):
                name = annotation_name(annotation)
                if name in forbidden:
                    line = (member.get("lines") or node.get("lines") or [None])[0]
                    where = node.get("file", "?") + (f":{line}" if line else "")
                    violations.append({"rule": "annotation", "scope": "module", "layer": layer, "annotation": name,
                                       "from": m.label(node_id), "from_id": node_id, "where": where})
    return violations, advisory, sorted(unresolved)


def project_root(start):
    for directory in [start, *start.parents]:
        if any((directory / name).is_file() for name in md.SETTINGS_FILES):
            return directory
    return None


def module_folder(map_path, m, root):
    """The module's folder: the map's module.dir under the project root, or the folder that holds docs/architecture
    when it is that same module."""
    relative = m.module.get("dir")
    if root is not None and relative:
        folder = root / relative
        if folder.is_dir():
            return folder
    if map_path is not None and map_path.parent.name == "architecture" and map_path.parent.parent.name == "docs":
        folder = map_path.parent.parent.parent
        if relative is None or (root is not None and folder.resolve() == (root / relative).resolve()):
            return folder
    return None


def newest_source(folder, map_path):
    """The newest source file of the module, skipping build output, nested modules and the map's folder."""
    newest, newest_time = None, -1.0
    for directory, subdirs, files in os.walk(folder):
        here = Path(directory)
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS and d != "docs"
                      and not any((here / d / name).is_file() for name in mapper.BUILD_FILES)]
        for name in files:
            path = here / name
            if path.suffix in SOURCE_SUFFIXES:
                modified = path.stat().st_mtime
                if modified > newest_time:
                    newest, newest_time = path, modified
    return newest, newest_time


def kotlin_modules(path):
    """Gradle modules under a folder that have Kotlin sources."""
    return [d for d in mapper.inner_modules(path) if any((d / "src").rglob("*.kt"))]


def load(paths, lang):
    loaded, errors = [], []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("nodes", []), list):
                raise ValueError("not a module map")
            loaded.append((path, data))
        except (OSError, ValueError) as error:
            errors.append(t(lang, "map_error", path=path, error=error))
    return loaded, errors


def check(selected, universe, rules, options, root, skip_freshness, generated, lang):
    maps = {id(data): build_map(data, rules, lang) for _, data in universe}
    index = {}
    prefixes = set()
    for _, data in universe:
        m = maps[id(data)]
        for node_id in m.nodes:
            index[node_id] = (m.path, m.layers[node_id])
        namespace = m.module.get("namespace") or ""
        if namespace.count(".") >= 1:
            prefixes.add(".".join(namespace.split(".")[:2]) + ".")
    prefixes = tuple(sorted(prefixes))
    results = []
    for path, data in selected:
        m = maps[id(data)]
        violations, advisory, unresolved = find_violations(m, {k: v for k, v in index.items() if k not in m.nodes},
                                                           prefixes, options)
        warnings = [w for w in data.get("warnings", []) if isinstance(w, str)]
        result = {
            "module": m.path,
            "module_dir": m.module.get("dir"),
            "map": None if generated else str(path),
            "violations": violations,
            "advisory": advisory,
            "unchecked": sorted(m.label(i) for i in md.listed_classes(m) if m.layers[i] == "Other"),
            "unresolved": unresolved,
            "partial_parse": [w for w in warnings if w.startswith(PARTIAL_PARSE_PREFIXES)],
            "warnings": [w for w in warnings if not w.startswith(PARTIAL_PARSE_PREFIXES)],
            "codegraph": (data.get("sources", {}).get("codegraph") or {}).get("status"),
            "freshness": "generated" if generated else "skipped" if skip_freshness else None,
            "stale_source": None,
        }
        if not generated and not skip_freshness:
            folder = module_folder(path, m, root)
            if folder is None:
                result["freshness"] = "unknown"
            else:
                source, modified = newest_source(folder, path)
                stale = source is not None and modified > path.stat().st_mtime + 1
                result["freshness"] = "stale" if stale else "fresh"
                result["stale_source"] = str(source) if stale else None
        results.append(result)
    return results


def missing_modules(path, root, results):
    """Kotlin modules under a module or project folder that no checked map covers."""
    if not path.is_dir() or root is None or any(path.glob("*.module-map.json")):
        return []
    covered = {(root / r["module_dir"]).resolve() for r in results if r["module_dir"]}
    return sorted(str(d.resolve().relative_to(root.resolve())).replace("\\", "/")
                  for d in kotlin_modules(path) if d.resolve() not in covered)


def summarize(results, missing, errors, options):
    violations = sum(len(r["violations"]) for r in results)
    incomplete = (not results or errors or missing
                  or any(r["freshness"] in ("stale", "unknown") or r["partial_parse"] for r in results)
                  or (options.fail_on_unresolved and any(r["unresolved"] for r in results))
                  or (options.fail_on_unchecked and any(r["unchecked"] for r in results)))
    if incomplete:
        return "incomplete", 2, violations
    if violations:
        return "violations", 1, violations
    return "pass", 0, violations


def print_text(results, missing, errors, status, violations, lang):
    for error in errors:
        print(error)
    for r in results:
        print(f"{r['module']}  ({r['map'] or t(lang, 'generated')})")
        for v in r["violations"]:
            if v["rule"] == "layer":
                line = t(lang, "violation_layer", source=v["from"], target=v["to"], pair=" -> ".join(v["layers"]),
                         edge=v["edge"], where=v["where"])
            elif v["rule"] == "external":
                line = t(lang, "violation_external", source=v["from"], target=v["to"], layer=v["layer"],
                         prefix=v["prefix"], edge=v["edge"], where=v["where"])
            else:
                line = t(lang, "violation_annotation", source=v["from"], layer=v["layer"], annotation=v["annotation"],
                         where=v["where"])
            print("  " + line)
        for a in r["advisory"]:
            print("  " + t(lang, "advisory", confidence=a["confidence"], source=a["from"], target=a["to"],
                            edge=a["edge"], where=a["where"]))
        if r["freshness"] == "stale":
            print("  " + t(lang, "stale", source=r["stale_source"]))
        elif r["freshness"] == "unknown":
            print("  " + t(lang, "module_not_found", dir=r["module_dir"] or r["module"]))
        if r["partial_parse"]:
            print("  " + t(lang, "partial_parse", files="; ".join(r["partial_parse"])))
        if r["unresolved"]:
            print("  " + t(lang, "unresolved", names=", ".join(r["unresolved"][:10])))
        if r["unchecked"]:
            print("  " + t(lang, "unchecked", count=len(r["unchecked"]), names=", ".join(r["unchecked"][:10])))
        if r["codegraph"] not in (None, "ok"):
            print("  " + t(lang, "no_codegraph"))
    if missing:
        print(t(lang, "missing_maps", modules=", ".join(missing)))
    if status == "incomplete":
        print(t(lang, "status_incomplete"))
    elif status == "violations":
        print(t(lang, "status_violations", count=violations, maps=len(results)))
    else:
        print(t(lang, "status_pass", maps=len(results)))


def generate_command(target, output, config_path, lang):
    """module_map.py into a temporary folder, without codegraph, whose index lives in the project."""
    command = [sys.executable, str(Path(__file__).with_name("module_map.py")), str(target), "-o", str(output),
               "--no-timestamp", "--no-codegraph", "--lang", lang]
    if config_path:
        command += ["--config", str(config_path)]
    return command


def generate_maps(target, output, config_path, lang):
    result = subprocess.run(generate_command(target, output, config_path, lang), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(t(lang, "generate_failed", error=(result.stderr or result.stdout).strip()), file=sys.stderr)
        return False
    return True


def under(module_dir, path, root):
    if root is None or module_dir is None:
        return True
    folder = (root / module_dir).resolve()
    target = path.resolve()
    return folder == target or folder.is_relative_to(target) or target.is_relative_to(folder)


def run(argv):
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--lang", choices=md.LANGS)
    known, _ = early.parse_known_args(argv)
    lang = md.requested_lang(known.lang) or md.DEFAULT_LANG
    cli = argparse.ArgumentParser(description=t(lang, "help"))
    cli.add_argument("path", type=Path, help=t(lang, "help_path"))
    cli.add_argument("--json", action="store_true", help=t(lang, "help_json"))
    cli.add_argument("--config", type=Path)
    cli.add_argument("--lang", choices=md.LANGS)
    cli.add_argument("--skip-freshness", action="store_true", help=t(lang, "help_skip_freshness"))
    cli.add_argument("--generate", action="store_true", help=t(lang, "help_generate"))
    args = cli.parse_args(argv)

    if args.config and not args.config.is_file():
        print(md.tr(lang, "config_not_found", path=args.config), file=sys.stderr)
        return 2
    start = args.path.resolve()
    config_path = args.config or md.find_config(start if start.is_dir() else start.parent)
    config = md.read_config(config_path, lang) if config_path else {}
    lang = md.requested_lang(args.lang) or config.get("lang") or md.DEFAULT_LANG
    if lang not in md.LANGS:
        lang = md.DEFAULT_LANG
    rules = md.rules_from_config(config, config_path, lang) if config else md.DEFAULT_RULES
    options = Options(config, config_path, lang)
    root = project_root(start if start.is_dir() else start.parent)

    with tempfile.TemporaryDirectory(prefix="module-check-") as scratch:
        if args.generate:
            if not args.path.is_dir():
                print(t(lang, "generate_needs_dir", path=args.path), file=sys.stderr)
                return 2
            if not generate_maps(root or args.path, Path(scratch), config_path, lang):
                return 2
            universe, errors = load(md.find_maps(Path(scratch)), lang)
            selected = [(p, d) for p, d in universe if under((d.get("module") or {}).get("dir"), args.path, root)]
        else:
            selected, errors = load(md.find_maps(args.path) if args.path.exists() else [], lang)
            others, more_errors = load(md.find_maps(root) if root else [], lang)
            chosen = {p.resolve() for p, _ in selected}
            universe = selected + [(p, d) for p, d in others if p.resolve() not in chosen]
            errors += [e for e in more_errors if e not in errors]
        try:
            results = check(selected, universe, rules, options, root, args.skip_freshness, args.generate, lang)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            errors.append(t(lang, "map_error", path=args.path, error=f"{type(error).__name__}: {error}"))
            results = []
    missing = missing_modules(args.path, root, results)
    status, code, violations = summarize(results, missing, errors, options)
    if args.json:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": status, "exit_code": code,
                          "violations": violations, "maps": results, "missing_maps": missing, "errors": errors},
                         indent=2, ensure_ascii=False))
    else:
        if not results and not errors:
            print(t(lang, "no_maps", path=args.path))
        print_text(results, missing, errors, status, violations, lang)
    return code


def main(argv=None):
    try:
        return run(argv)
    except SystemExit as stop:
        # module_diagrams stops with a message (exit code 1) on invalid configuration; here 1 means violations.
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
            return 2
        return stop.code if isinstance(stop.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
