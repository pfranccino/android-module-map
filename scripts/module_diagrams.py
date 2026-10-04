#!/usr/bin/env python3
"""
module_diagrams.py: turns the JSON from module_map.py into a Markdown document with five
Mermaid diagrams (layers, flows from the ViewModels, sequence of a flow, module dependencies,
Hilt injection) and an analysis (classes, layer violations, entry points, external
dependencies, edges to review). --only selects the sections; --sections lists them.

It is deterministic: the same map always produces the same document. It only draws what is in
the map; it does not interpret or fill in anything.

Usage: python module_diagrams.py <module_directory> [--only classes] [--flow Class.function]
       python module_diagrams.py <directory_with_several_modules> [--module engine] [--only classes]
       python module_diagrams.py <map.json | directory_of_maps> [-o output] [--lang en]

Given a module (or a directory of modules) it reads the maps that module_map.py left in each
<module>/docs/architecture/ and writes every document next to its map. With several modules it
also writes an index.md with the graph of Gradle dependencies between them. --module restricts it to one module. With --only the result
goes to <module>.<sections>.md, so the full document is never overwritten, and modules that have
nothing for those sections get no file.

The layer rules, the size above which the layer diagram is drawn by package, and the language
can be set in a .module-map.toml file at the root of the Android project.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

LAYERS = ["Presentation", "Domain", "Data", "DI", "Other"]
LAYER_BY_SEGMENT = {
    "ui": "Presentation", "presentation": "Presentation", "view": "Presentation",
    "screen": "Presentation", "compose": "Presentation", "fragment": "Presentation",
    "activity": "Presentation", "adapter": "Presentation", "widget": "Presentation",
    "viewmodel": "Presentation",
    "domain": "Domain", "usecase": "Domain", "interactor": "Domain",
    "data": "Data", "network": "Data", "remote": "Data", "api": "Data",
    "local": "Data", "cache": "Data", "db": "Data", "database": "Data",
    "datasource": "Data", "repository": "Data",
    "di": "DI", "hilt": "DI", "injection": "DI",
}
LAYER_BY_ROLE = {"activity": "Presentation", "fragment": "Presentation", "composable": "Presentation",
                 "viewmodel": "Presentation", "android_entry": "Presentation",
                 "adapter": "Presentation", "viewholder": "Presentation",
                 "binding_adapter": "Presentation",
                 "ui_state": "Presentation", "ui_event": "Presentation",
                 "navigator": "Presentation",
                 "usecase": "Domain", "mapper": "Domain",
                 "datasource": "Data", "api_service": "Data", "dao": "Data",
                 "entity": "Data", "database": "Data", "type_converter": "Data",
                 "model": "Data",
                 "di_module": "DI"}
# (source layer, target layer) pairs that break the dependency rule. DI is exempt.
FORBIDDEN = {("Presentation", "Data"), ("Domain", "Presentation"), ("Domain", "Data"),
             ("Data", "Presentation")}
HILT_ENTRY_POINTS = ("HiltViewModel", "AndroidEntryPoint")
DOCS_DIR = "docs/architecture"  # where module_map.py leaves each map by default, inside its module
READABLE_LIMIT = 40  # node count above which the layer diagram is drawn by package
CONFIG_FILE = ".module-map.toml"
SETTINGS_FILES = ("settings.gradle.kts", "settings.gradle")
LANGS = ("es", "en")
DEFAULT_LANG = "es"
# Light fills with dark text: legible in both the light and the dark theme of GitHub.
LAYER_STYLES = {
    "Presentation": "fill:#dbeafe,stroke:#2563eb,color:#0f172a",
    "Domain": "fill:#dcfce7,stroke:#16a34a,color:#0f172a",
    "Data": "fill:#fef3c7,stroke:#d97706,color:#0f172a",
    "DI": "fill:#f3e8ff,stroke:#9333ea,color:#0f172a",
    "Other": "fill:#f1f5f9,stroke:#64748b,color:#0f172a",
}
EXTERNAL_STYLE = "fill:#ffffff,stroke:#94a3b8,stroke-dasharray:4,color:#334155"

MESSAGES = {
    "es": {
        "module_fallback": "módulo",
        "default_package": "(sin paquete)",
        "nodes_count": "{count} nodos",
        "implemented_by": "implementado por",
        "extends": "extiende",
        "creates": "crea",
        "uses": "usa",
        "injects": "inyecta",
        "other_modules": "Otros módulos",
        "libraries": "Librerías",
        "provided_outside": "Provisto fuera de este módulo",
        "layers_heading": "## Arquitectura por capas",
        "layers_legend": "Flecha sólida: depende de o llama a. Flecha punteada: relación de tipos.",
        "layers_empty": "El mapa no tiene nodos con rol ni dependencias entre clases que dibujar.",
        "layers_by_package": "El módulo tiene {count} nodos, más de {limit}, así que el diagrama muestra paquetes "
                             "en vez de clases. Cada flecha indica cuántas dependencias o llamadas van de un paquete "
                             "a otro. Para dibujar las clases, sube `layers.readable_limit` en `.module-map.toml` o "
                             "mapea un paquete (pasando a module_map.py su directorio).",
        "flows_heading": "## Flujos desde los ViewModels",
        "flows_empty": "El mapa no registra llamadas fiables desde un ViewModel de este módulo.",
        "flows_legend": "La etiqueta de cada flecha que sale de un ViewModel es la función que inicia la llamada. "
                        "Cada implementación se dibuja como su interfaz. Solo llamadas dentro del módulo que el "
                        "AST confirmó por el tipo del receptor o que codegraph resolvió con confianza de 0.9 o más.",
        "flows_large": "El diagrama tiene {count} nodos; para verlo por partes, mapea un paquete.",
        "sequence_heading": "## Secuencia",
        "sequence_heading_named": "## Secuencia: {name}",
        "sequence_empty": "El mapa no tiene llamadas (`calls`) que salgan de este módulo, así que no hay flujo que "
                          "dibujar. Las llamadas vienen de codegraph: revisa en «Aristas a revisar» si se usó.",
        "sequence_note": "Solo llamadas entre clases, en orden de línea. No refleja ramas, bucles ni asincronía; "
                         "otro flujo con `--flow Clase.funcion`.",
        "no_calls_from": "No hay llamadas registradas desde {flow}. Flujos disponibles: {options}",
        "none": "ninguno",
        "modules_heading": "## Dependencias entre módulos",
        "modules_legend": "Salientes: declaradas en Gradle. Entrantes (`usa`): usos observados en el código.",
        "hilt_heading": "## Inyección de dependencias",
        "hilt_empty": "El mapa no registra `@Provides`, `@Binds` ni dependencias con `@Inject`.",
        "classes_heading": "## Clases",
        "classes_empty": "El mapa no tiene clases.",
        "classes_columns": "Clase|Tipo|Capa|Rol|Responsabilidad (KDoc)",
        "no_kdoc": "(sin KDoc)",
        "violations_heading": "## Violaciones de capas",
        "violations_none": "No se encontraron violaciones. Se revisaron todas las aristas entre clases del módulo "
                           "contra estas dependencias prohibidas: {rules}.",
        "violations_no_rules": "No hay reglas de capas: `layers.forbidden` está vacío en `.module-map.toml`.",
        "violations_columns": "Arista|Capas|Tipo|Evidencia|Nota",
        "confidence_unverified": "confianza {value}, sin verificar",
        "entries_heading": "## Puntos de entrada",
        "entries_empty": "El mapa no registra componentes en el Manifest ni usos desde otros módulos.",
        "manifest_components": "**Componentes del Manifest**",
        "manifest_columns": "Tipo|Clase|Exported",
        "exported_yes": "sí",
        "exported_no": "no",
        "exported_unset": "sin declarar",
        "used_from_modules": "**Usado desde otros módulos**",
        "used_from_columns": "Elemento de este módulo|Lo usa|Módulo|Tipo",
        "external_heading": "## Dependencias externas",
        "external_empty": "El mapa no registra dependencias externas.",
        "external_columns": "Origen|Tipo externo|Usado por",
        "library": "librería",
        "declared_unused": "Declarados en Gradle sin uso observado en el código Kotlin (el mapa no ve recursos ni "
                           "código generado, así que no implica que sobren): {modules}.",
        "used_undeclared": "Usados en código sin estar declarados directamente en este módulo (probable dependencia "
                           "transitiva): {modules}.",
        "review_heading": "## Aristas a revisar",
        "review_empty": "Nada que revisar.",
        "basis_import": "el import del archivo",
        "basis_receiver": "el tipo declarado del receptor",
        "review_corrected": "Corregida: `{arrow}` ({source} → {target}, {where}). codegraph proponía `{proposed}`; "
                            "se usó {basis}.",
        "review_confidence": "Confianza {confidence}: `{arrow}` ({kind}: {source} → {target}, {where}). Resuelta por "
                             "nombre; revisar en el código.",
        "review_warning": "Aviso del mapa: {warning}",
        "review_no_ast": "Sin AST (solo datos de codegraph): `{label}` en {file}.",
        "review_gap": "Posible vacío: `{label}` tiene rol {role} pero ninguna relación en el mapa.",
        "review_discarded": "codegraph: aristas descartadas: {count}. Nada en el archivo que llama respaldaba el "
                            "destino (ni tipo declarado, ni import, ni mismo paquete).",
        "review_no_codegraph": "codegraph no se usó ({status}): el mapa no tiene llamadas, instanciaciones ni "
                               "referencias, solo la estructura leída del AST.",
        "doc_title": "# {path}: arquitectura",
        "doc_intro": "Generado por `module_diagrams.py` a partir del mapa del {date}. {nodes} nodos y {edges} "
                     "aristas. No editar a mano: se regenera desde el mapa.",
        "index_title": "# {title}: módulos",
        "index_intro": "Generado por `module_diagrams.py` a partir de {count} mapas. No editar a mano.",
        "index_legend": "Solo dependencias declaradas en Gradle entre los módulos de este grupo. Una flecha sin "
                        "etiqueta es `implementation`. Las dependencias hacia otros módulos, los usos en el código "
                        "y el detalle por clase están en el documento de cada módulo.",
        "index_modules_heading": "## Módulos",
        "index_relative": "Nombres relativos a `{group}`. ",
        "index_links": "Cada módulo enlaza a su documento.",
        "index_columns": "Módulo|Tipo|Nodos|Depende de (en este grupo)|Otros módulos",
        "section_layers": "diagrama de arquitectura por capas",
        "section_flows": "diagrama de flujos desde los ViewModels",
        "section_sequence": "diagrama de secuencia de un flujo",
        "section_modules": "diagrama de dependencias del módulo",
        "section_hilt": "diagrama de inyección de dependencias",
        "section_classes": "tabla de todas las clases con tipo, capa, rol y KDoc",
        "section_violations": "violaciones de capas",
        "section_entries": "puntos de entrada",
        "section_external": "dependencias externas",
        "section_review": "aristas a revisar",
        "cli_description": "Diagramas Mermaid y análisis a partir del mapa de module_map.py.",
        "help_map": "directorio del módulo (o de varios módulos), directorio de mapas, o un mapa JSON",
        "help_output": "directorio de salida (por defecto, junto a cada mapa); con un solo módulo también puede "
                       "ser el archivo .md",
        "help_module": "con varios módulos, procesa solo ese (por ejemplo engine o :core:engine)",
        "help_only": "secciones a generar, separadas por coma (por defecto, todas): {sections}. Se escriben en "
                     "<modulo>.<secciones>.md, sin tocar el documento completo",
        "help_flow": "flujo del diagrama de secuencia, como LoginViewModel.submit (un solo módulo)",
        "help_sections": "lista las secciones disponibles y termina",
        "help_lang": "idioma del documento y de los mensajes; si no se indica, MODULE_MAP_LANG, luego `lang` en "
                     ".module-map.toml, luego es",
        "help_config": "archivo .module-map.toml; por defecto se busca desde la ruta dada hasta la raíz del proyecto",
        "missing_map": "falta el módulo, el directorio o el mapa",
        "not_found": "No existe {path}",
        "unknown_sections": "Sección desconocida: {unknown}. Disponibles: {available}",
        "no_maps": "No hay mapas en {path}. Este script los lee, no los crea: ejecuta antes module_map.py {path}",
        "no_module_map": "Ningún mapa es del módulo {module}. Disponibles: {available}",
        "flow_single_module": "--flow solo aplica a un módulo: pasa ese módulo o usa --module",
        "output_must_be_dir": "Hay {count} módulos: -o debe ser un directorio, o usa --module para elegir uno",
        "generated": "generado: {path}",
        "skipped": "sin contenido para {sections}, no se generó documento: {modules}",
        "bad_lang": "Idioma no soportado: {value!r}. Disponibles: {langs}",
        "config_not_found": "No existe el archivo de configuración: {path}",
        "config_no_toml": "{file} necesita Python 3.11+ o el paquete tomli para leerse",
        "config_invalid_toml": "{file} no es TOML válido: {error}",
        "config_unknown": "{file}: clave desconocida `{key}`",
        "config_invalid_value": "{file}: valor no válido en `{key}`: {value!r}. {hint}",
        "hint_layers": "Capas válidas: {layers}.",
        "hint_forbidden": "Debe ser una lista de pares [origen, destino] con capas de: {layers}.",
        "hint_limit": "Debe ser un entero positivo.",
    },
    "en": {
        "module_fallback": "module",
        "default_package": "(no package)",
        "nodes_count": "{count} nodes",
        "implemented_by": "implemented by",
        "extends": "extends",
        "creates": "creates",
        "uses": "uses",
        "injects": "injects",
        "other_modules": "Other modules",
        "libraries": "Libraries",
        "provided_outside": "Provided outside this module",
        "layers_heading": "## Layered architecture",
        "layers_legend": "Solid arrow: depends on or calls. Dotted arrow: type relationship.",
        "layers_empty": "The map has no nodes with a role or dependencies between classes to draw.",
        "layers_by_package": "The module has {count} nodes, more than {limit}, so the diagram shows packages instead "
                             "of classes. Each arrow tells how many dependencies or calls go from one package to "
                             "another. To draw the classes, raise `layers.readable_limit` in `.module-map.toml` or "
                             "map one package (passing its directory to module_map.py).",
        "flows_heading": "## Flows from the ViewModels",
        "flows_empty": "The map has no reliable calls from a ViewModel of this module.",
        "flows_legend": "The label of each arrow leaving a ViewModel is the function that starts the call. Each "
                        "implementation is drawn as its interface. Only calls inside the module that the AST "
                        "confirmed through the receiver type, or that codegraph resolved with confidence 0.9 or more.",
        "flows_large": "The diagram has {count} nodes; to see it in parts, map one package.",
        "sequence_heading": "## Sequence",
        "sequence_heading_named": "## Sequence: {name}",
        "sequence_empty": "The map has no calls (`calls`) leaving this module, so there is no flow to draw. Calls "
                          "come from codegraph: check under \"Edges to review\" whether it was used.",
        "sequence_note": "Only calls between classes, in line order. Branches, loops and asynchrony are not shown; "
                         "pick another flow with `--flow Class.function`.",
        "no_calls_from": "No calls recorded from {flow}. Available flows: {options}",
        "none": "none",
        "modules_heading": "## Module dependencies",
        "modules_legend": "Outgoing: declared in Gradle. Incoming (`uses`): usages observed in the code.",
        "hilt_heading": "## Dependency injection",
        "hilt_empty": "The map has no `@Provides`, `@Binds` or `@Inject` dependencies.",
        "classes_heading": "## Classes",
        "classes_empty": "The map has no classes.",
        "classes_columns": "Class|Kind|Layer|Role|Responsibility (KDoc)",
        "no_kdoc": "(no KDoc)",
        "violations_heading": "## Layer violations",
        "violations_none": "No violations found. Every edge between classes of the module was checked against these "
                           "forbidden dependencies: {rules}.",
        "violations_no_rules": "There are no layer rules: `layers.forbidden` is empty in `.module-map.toml`.",
        "violations_columns": "Edge|Layers|Kind|Evidence|Note",
        "confidence_unverified": "confidence {value}, unverified",
        "entries_heading": "## Entry points",
        "entries_empty": "The map has no Manifest components and no usages from other modules.",
        "manifest_components": "**Manifest components**",
        "manifest_columns": "Type|Class|Exported",
        "exported_yes": "yes",
        "exported_no": "no",
        "exported_unset": "not declared",
        "used_from_modules": "**Used from other modules**",
        "used_from_columns": "Element of this module|Used by|Module|Kind",
        "external_heading": "## External dependencies",
        "external_empty": "The map has no external dependencies.",
        "external_columns": "Origin|External type|Used by",
        "library": "library",
        "declared_unused": "Declared in Gradle with no usage seen in the Kotlin code (the map does not see resources "
                           "or generated code, so this does not mean they are unneeded): {modules}.",
        "used_undeclared": "Used in code without being declared directly in this module (probably a transitive "
                           "dependency): {modules}.",
        "review_heading": "## Edges to review",
        "review_empty": "Nothing to review.",
        "basis_import": "the import of the file",
        "basis_receiver": "the declared type of the receiver",
        "review_corrected": "Corrected: `{arrow}` ({source} → {target}, {where}). codegraph proposed `{proposed}`; "
                            "{basis} was used instead.",
        "review_confidence": "Confidence {confidence}: `{arrow}` ({kind}: {source} → {target}, {where}). Resolved by "
                             "name; check it in the code.",
        "review_warning": "Map warning: {warning}",
        "review_no_ast": "No AST (codegraph data only): `{label}` in {file}.",
        "review_gap": "Possible gap: `{label}` has role {role} but no relationship in the map.",
        "review_discarded": "codegraph: discarded edges: {count}. Nothing in the calling file backed the target "
                            "(no declared type, no import, not the same package).",
        "review_no_codegraph": "codegraph was not used ({status}): the map has no calls, instantiations or "
                               "references, only the structure read from the AST.",
        "doc_title": "# {path}: architecture",
        "doc_intro": "Generated by `module_diagrams.py` from the map of {date}. {nodes} nodes and {edges} edges. "
                     "Do not edit by hand: it is regenerated from the map.",
        "index_title": "# {title}: modules",
        "index_intro": "Generated by `module_diagrams.py` from {count} maps. Do not edit by hand.",
        "index_legend": "Only dependencies declared in Gradle between the modules of this group. An arrow without a "
                        "label is `implementation`. Dependencies on other modules, usages in the code and the "
                        "class-level detail are in the document of each module.",
        "index_modules_heading": "## Modules",
        "index_relative": "Names relative to `{group}`. ",
        "index_links": "Each module links to its document.",
        "index_columns": "Module|Type|Nodes|Depends on (in this group)|Other modules",
        "section_layers": "layered architecture diagram",
        "section_flows": "diagram of the flows from the ViewModels",
        "section_sequence": "sequence diagram of one flow",
        "section_modules": "module dependency diagram",
        "section_hilt": "dependency injection diagram",
        "section_classes": "table of every class with kind, layer, role and KDoc",
        "section_violations": "layer violations",
        "section_entries": "entry points",
        "section_external": "external dependencies",
        "section_review": "edges to review",
        "cli_description": "Mermaid diagrams and analysis from the map written by module_map.py.",
        "help_map": "directory of the module (or of several modules), directory of maps, or one map JSON",
        "help_output": "output directory (by default, next to each map); with a single module it can also be the "
                       ".md file",
        "help_module": "with several modules, process only this one (for example engine or :core:engine)",
        "help_only": "sections to generate, comma separated (all by default): {sections}. They are written to "
                     "<module>.<sections>.md, without touching the full document",
        "help_flow": "flow of the sequence diagram, such as LoginViewModel.submit (one module)",
        "help_sections": "list the available sections and exit",
        "help_lang": "language of the document and the messages; when absent, MODULE_MAP_LANG, then `lang` in "
                     ".module-map.toml, then es",
        "help_config": ".module-map.toml file; by default it is looked up from the given path to the project root",
        "missing_map": "the module, directory or map is missing",
        "not_found": "{path} does not exist",
        "unknown_sections": "Unknown section: {unknown}. Available: {available}",
        "no_maps": "No maps in {path}. This script reads them, it does not create them: run module_map.py {path} "
                   "first",
        "no_module_map": "No map belongs to module {module}. Available: {available}",
        "flow_single_module": "--flow only applies to one module: pass that module or use --module",
        "output_must_be_dir": "There are {count} modules: -o must be a directory, or use --module to pick one",
        "generated": "generated: {path}",
        "skipped": "nothing for {sections}, no document written: {modules}",
        "bad_lang": "Unsupported language: {value!r}. Available: {langs}",
        "config_not_found": "Configuration file not found: {path}",
        "config_no_toml": "{file} needs Python 3.11+ or the tomli package to be read",
        "config_invalid_toml": "{file} is not valid TOML: {error}",
        "config_unknown": "{file}: unknown key `{key}`",
        "config_invalid_value": "{file}: invalid value for `{key}`: {value!r}. {hint}",
        "hint_layers": "Valid layers: {layers}.",
        "hint_forbidden": "It must be a list of [source, target] pairs with layers from: {layers}.",
        "hint_limit": "It must be a positive integer.",
    },
}


def tr(lang, message, /, **values):
    return MESSAGES[lang][message].format(**values)


# ---------- configuration ----------

@dataclass(frozen=True)
class Rules:
    by_segment: dict
    by_role: dict
    forbidden: frozenset
    readable_limit: int


DEFAULT_RULES = Rules(LAYER_BY_SEGMENT, LAYER_BY_ROLE, frozenset(FORBIDDEN), READABLE_LIMIT)


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


def find_config(start):
    """.module-map.toml in `start` or one of its parents, stopping at the project root (settings.gradle)."""
    for directory in [start, *start.parents]:
        if (directory / CONFIG_FILE).is_file():
            return directory / CONFIG_FILE
        if any((directory / f).is_file() for f in SETTINGS_FILES):
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


def rules_from_config(config, path, lang):
    """The defaults, overridden by the [layers] table. An unknown key stops the run: a typo would otherwise
    be ignored without a word."""
    def invalid(key, value, hint):
        sys.exit(tr(lang, "config_invalid_value", file=path, key=key, value=value, hint=hint))

    for key in config:
        if key not in ("lang", "layers"):
            sys.exit(tr(lang, "config_unknown", file=path, key=key))
    layers = config.get("layers", {})
    layer_hint = tr(lang, "hint_layers", layers=", ".join(LAYERS))
    if not isinstance(layers, dict):
        invalid("layers", layers, layer_hint)
    for key in layers:
        if key not in ("by_segment", "by_role", "forbidden", "readable_limit"):
            sys.exit(tr(lang, "config_unknown", file=path, key=f"layers.{key}"))
    merged = {}
    for table, defaults in (("by_segment", LAYER_BY_SEGMENT), ("by_role", LAYER_BY_ROLE)):
        overrides = layers.get(table, {})
        if not isinstance(overrides, dict):
            invalid(f"layers.{table}", overrides, layer_hint)
        for name, layer in overrides.items():
            if layer not in LAYERS:
                invalid(f"layers.{table}.{name}", layer, layer_hint)
        merged[table] = {**defaults, **overrides}
    forbidden = layers.get("forbidden", [list(pair) for pair in sorted(FORBIDDEN)])
    if not isinstance(forbidden, list) or not all(
            isinstance(pair, list) and len(pair) == 2 and all(layer in LAYERS for layer in pair) for pair in forbidden):
        invalid("layers.forbidden", forbidden, tr(lang, "hint_forbidden", layers=", ".join(LAYERS)))
    limit = layers.get("readable_limit", READABLE_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        invalid("layers.readable_limit", limit, tr(lang, "hint_limit"))
    return Rules(merged["by_segment"], merged["by_role"], frozenset(tuple(pair) for pair in forbidden), limit)


# ---------- the map ----------

class Map:
    def __init__(self, data, rules=DEFAULT_RULES, lang=DEFAULT_LANG):
        self.data = data
        self.rules = rules
        self.lang = lang
        self.module = data.get("module", {})
        self.path = self.module.get("path") or self.module.get("dir") or self.t("module_fallback")
        self.nodes = {n["id"]: n for n in data.get("nodes", [])}
        self.externals = {n["id"]: n for n in data.get("external_nodes", [])}
        self.edges = data.get("edges", [])
        self.implementers = defaultdict(list)  # interface -> implementations inside the module
        for e in self.edges:
            if e["kind"] == "implements" and e["from"] in self.nodes:
                self.implementers[e["to"]].append(e["from"])
        self.layers = {node_id: self.layer(node) for node_id, node in self.nodes.items()}
        self._propagate_layers()

    def t(self, message, /, **values):
        return tr(self.lang, message, **values)

    def _propagate_layers(self):
        """Infer layer for 'Other' nodes from their already-classified neighbors."""
        neighbors = defaultdict(list)
        for e in self.edges:
            if e["from"] in self.nodes and e["to"] in self.nodes:
                neighbors[e["from"]].append(e["to"])
                neighbors[e["to"]].append(e["from"])
        changed = True
        while changed:
            changed = False
            for node_id, current in list(self.layers.items()):
                if current != "Other":
                    continue
                votes = {}
                for neighbor in neighbors.get(node_id, []):
                    n_layer = self.layers.get(neighbor)
                    if n_layer and n_layer != "Other":
                        votes[n_layer] = votes.get(n_layer, 0) + 1
                if not votes:
                    continue
                total = sum(votes.values())
                best = max(votes, key=votes.get)
                if votes[best] / total >= 0.7 and total >= 2:
                    self.layers[node_id] = best
                    changed = True

    def layer(self, node):
        """Layer by package segment, role, import-based hint, or 'Other' as last resort."""
        packages = [s for s in node["id"].split(".")[:-1] if s[:1].islower()]
        for segment in reversed(packages):
            if segment in self.rules.by_segment:
                return self.rules.by_segment[segment]
        if node.get("role") == "repository":
            return "Domain" if node["kind"] == "interface" else "Data"
        role_layer = self.rules.by_role.get(node.get("role"))
        if role_layer:
            return role_layer
        if node.get("layer_hint"):
            return node["layer_hint"]
        return "Other"

    def label(self, node_id, _seen=None):
        node = self.nodes.get(node_id) or self.externals.get(node_id) or {"name": node_id}
        parent = node.get("parent")
        if parent:
            seen = _seen or set()
            if parent in seen:  # cycle in parent references: stop to avoid infinite recursion
                return node["name"]
            seen.add(node_id)
            return f"{self.label(parent, seen)}.{node['name']}"
        return node["name"]

    def where(self, edge):
        """Evidence for an edge: file of the source node and lines from its details."""
        file = (self.nodes.get(edge["from"]) or self.externals.get(edge["from"]) or {}).get("file", "?")
        lines = sorted({d["line"] for d in edge.get("details", []) if "line" in d})
        return f"{file}:{','.join(map(str, lines))}" if lines else file


class Ids:
    """Short, stable ids for Mermaid: n0, n1, ... in order of first appearance."""

    def __init__(self):
        self.ids = {}

    def __call__(self, node_id):
        return self.ids.setdefault(node_id, f"n{len(self.ids)}")


def quote(text):
    return text.replace('"', "'")


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def columns(m, key):
    return m.t(key).split("|")


def mermaid(lines):
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def styles(groups):
    """classDef and class lines for Mermaid. groups: [(class name, style, [Mermaid ids])]; empty groups are left out."""
    lines = []
    for name, style, members in groups:
        if members:
            lines.append(f"    classDef {name} {style}")
            lines.append(f"    class {','.join(members)} {name}")
    return lines


def layer_styles(ids, layer_of, items):
    """One color per layer for the given items (node ids or packages) already declared in the diagram."""
    return styles([(f"layer{layer}", LAYER_STYLES[layer], [ids(i) for i in items if layer_of[i] == layer])
                   for layer in LAYERS])


# ---------- 1. layers ----------

def drawn_nodes(m):
    """Nodes of the layer diagram: with a role or with dependencies; no models, nested types or private helpers."""
    linked = {end for e in m.edges if e["kind"] in ("depends_on", "calls") for end in (e["from"], e["to"])}
    shown = []
    for node_id, node in m.nodes.items():
        if node.get("parent") or node["kind"] not in ("class", "interface", "object", "function"):
            continue
        if "data" in node.get("modifiers", []):
            continue
        if node["kind"] == "function" and node.get("visibility") == "private":
            continue
        if node.get("role") or node_id in linked:
            shown.append(node_id)
    return sorted(shown, key=lambda i: (LAYERS.index(m.layers[i]), m.nodes[i]["file"], m.nodes[i].get("lines", [0])[0]))


def layered_diagram(m):
    shown = drawn_nodes(m)
    if not shown:
        return m.t("layers_empty"), 0
    if len(shown) > m.rules.readable_limit:
        return package_diagram(m, shown), len(shown)
    inside = set(shown)
    ids = Ids()
    arrows, plain, externals = [], set(), []
    for e in m.edges:
        src, dst, kind = e["from"], e["to"], e["kind"]
        if src not in inside:
            continue
        if kind in ("depends_on", "calls") and (dst in inside or dst in m.externals):
            if dst in m.externals and dst not in externals:
                externals.append(dst)
            plain.add((src, dst))
        elif dst not in inside:
            continue
        elif kind == "implements":  # the map stores impl -> interface; drawn reversed to keep the layers in order
            arrows.append(f"    {ids(dst)} -.{m.t('implemented_by')}.-> {ids(src)}")
        elif kind == "extends":
            arrows.append(f"    {ids(src)} -.{m.t('extends')}.-> {ids(dst)}")
        elif kind == "instantiates":
            arrows.append(f"    {ids(src)} -->|{m.t('creates')}| {ids(dst)}")
    for e in m.edges:  # uses_type only when it is the sole link of a composable (typically screen -> ViewModel)
        if (e["kind"] == "uses_type" and e["from"] in inside and e["to"] in inside
                and m.nodes[e["from"]].get("role") == "composable"):
            plain.add((e["from"], e["to"]))

    lines = ["graph TD"]
    for layer in LAYERS:
        members = [i for i in shown if m.layers[i] == layer]
        if members:
            lines.append(f'    subgraph {layer}')
            lines += [f'        {ids(i)}["{quote(m.label(i))}"]' for i in members]
            lines.append("    end")
    groups = (("ext_modules", m.t("other_modules"), "project"), ("ext_libraries", m.t("libraries"), "library"))
    for group_id, title, origin in groups:
        members = sorted(i for i in externals if m.externals[i].get("origin") == origin)
        if members:
            lines.append(f'    subgraph {group_id}["{title}"]')
            for i in members:
                module = m.externals[i].get("module")
                suffix = f"<br/>{module}" if module else ""
                lines.append(f'        {ids(i)}["{quote(m.label(i))}{suffix}"]')
            lines.append("    end")
    lines += sorted(f"    {ids(src)} --> {ids(dst)}" for src, dst in plain) + sorted(arrows)
    lines += layer_styles(ids, m.layers, shown) + styles([("external", EXTERNAL_STYLE, [ids(i) for i in externals])])
    return mermaid(lines), len(shown)


def package_of(node_id):
    """com.acme.ui.LoginScreen@12 -> com.acme.ui"""
    return node_id.split("@")[0].rpartition(".")[0]


def short_names(packages):
    """Package names without the prefix they all share, keeping at least the last segment of each."""
    split = [package.split(".") for package in packages]
    shared = 0
    for parts in zip(*split):
        if len(set(parts)) > 1:
            break
        shared += 1
    shared = min(shared, min(len(parts) for parts in split) - 1)
    return {package: ".".join(parts[shared:]) for package, parts in zip(packages, split)}


def package_diagram(m, shown):
    """Overview for large modules: one box per package, and arrows that count the dependencies and calls
    between packages. A package goes to the layer most of its nodes belong to."""
    members = defaultdict(list)
    for node_id in shown:
        members[package_of(node_id)].append(node_id)
    layer_of = {}
    for package, nodes in members.items():
        counts = Counter(m.layers[n] for n in nodes)
        layer_of[package] = max(LAYERS, key=lambda layer: (counts[layer], -LAYERS.index(layer)))
    inside = set(shown)
    links = Counter()
    for e in m.edges:
        if e["kind"] not in ("depends_on", "calls") or e["from"] not in inside:
            continue
        source = package_of(e["from"])
        if e["to"] in inside:
            if package_of(e["to"]) != source:
                links[(source, package_of(e["to"]))] += 1
        elif m.externals.get(e["to"], {}).get("origin") == "project" and m.externals[e["to"]].get("module"):
            links[(source, m.externals[e["to"]]["module"])] += 1  # Gradle paths start with ':', packages never do

    packages = sorted(members)
    short = short_names(packages)
    ids = Ids()
    lines = ["graph TD"]
    for layer in LAYERS:
        in_layer = [p for p in packages if layer_of[p] == layer]
        if in_layer:
            lines.append(f"    subgraph {layer}")
            lines += [f'        {ids(p)}["{quote(short[p] or m.t("default_package"))}<br/>'
                      f'{m.t("nodes_count", count=len(members[p]))}"]' for p in in_layer]
            lines.append("    end")
    modules = sorted({target for _, target in links if target.startswith(":")})
    if modules:
        lines.append(f'    subgraph ext_modules["{m.t("other_modules")}"]')
        lines += [f'        {ids(module)}["{module}"]' for module in modules]
        lines.append("    end")
    lines += [f"    {ids(a)} -->|{count}| {ids(b)}" for (a, b), count in sorted(links.items())]
    lines += layer_styles(ids, layer_of, packages) + styles([("external", EXTERNAL_STYLE, [ids(x) for x in modules])])
    return mermaid(lines)


# ---------- 2. flows from the ViewModels ----------

def trusted_call(edge):
    """Trusted call: the AST confirmed the receiver type, or codegraph resolved it with high confidence."""
    if "ast" in edge["provenance"]:
        return True
    details = edge.get("details", [])
    return bool(details) and all(d.get("confidence", 0) >= 0.9 for d in details)


def flow_diagram(m):
    """ViewModel --function--> use case --> repository --> ... Each implementation is drawn as its interface."""
    shown = {}  # implementation -> module interface that represents it
    for interface, implementations in m.implementers.items():
        if interface in m.nodes:
            for implementation in implementations:
                shown.setdefault(implementation, interface)
    calls = defaultdict(list)
    for e in m.edges:
        if e["kind"] == "calls" and e["from"] in m.nodes and e["to"] in m.nodes and trusted_call(e):
            calls[e["from"]].append(e)
    roots = sorted((i for i, n in m.nodes.items() if n.get("role") == "viewmodel" and calls.get(i)),
                   key=lambda i: (m.nodes[i]["file"], m.nodes[i].get("lines", [0])[0]))
    if not roots:
        return None, 0

    arrows, order, pending = {}, list(roots), deque(roots)  # (source, target) -> ViewModel functions
    while pending:
        node = pending.popleft()
        for source in [node, *sorted(m.implementers.get(node, []))]:  # an interface continues via its implementations
            for e in calls.get(source, []):
                target = shown.get(e["to"], e["to"])
                if target == node:
                    continue
                functions = arrows.setdefault((node, target), [])
                if node in roots:
                    functions += [d["from"] for d in e.get("details", []) if d["from"] not in functions]
                if target not in order:
                    order.append(target)
                    pending.append(target)

    ids = Ids()
    lines = ["graph LR"] + [f'    {ids(i)}["{quote(m.label(i))}"]' for i in order]
    for (src, dst), functions in arrows.items():
        label = ", ".join(functions[:3]) + (", …" if len(functions) > 3 else "")
        lines.append(f'    {ids(src)} -->|"{label}"| {ids(dst)}' if label else f"    {ids(src)} --> {ids(dst)}")
    lines += layer_styles(ids, m.layers, order)
    return mermaid(lines), len(order)


# ---------- 3. sequence ----------

def call_index(m):
    """(node, function) -> [(line, target node, target function)], from the details of the calls edges."""
    calls = defaultdict(list)
    for e in m.edges:
        if e["kind"] == "calls":
            for d in e.get("details", []):
                calls[(e["from"], d["from"])].append((d.get("line", 0), e["to"], d["to"]))
    for targets in calls.values():
        targets.sort()
    return calls


def trace(m, calls, node, function, seen):
    """Walk the calls depth-first. Step = (source, target, function); function None = hop to the implementation."""
    steps = []
    for _, dst, dst_function in calls.get((node, function), []):
        steps.append((node, dst, dst_function))
        if (dst, dst_function) not in calls and m.implementers.get(dst):
            implementation = sorted(m.implementers[dst])[0]
            steps.append((dst, implementation, None))
            dst = implementation
        if (dst, dst_function) not in seen:
            seen.add((dst, dst_function))
            steps += trace(m, calls, dst, dst_function, seen)
    return steps


def pick_flow(m, calls, requested):
    """Flow requested with --flow, or the function with the longest call chain (ViewModels first)."""
    roots = sorted(key for key in calls if key[0] in m.nodes)
    if requested:
        match = [k for k in roots if f"{m.label(k[0])}.{k[1]}" == requested]
        if not match:
            options = ", ".join(f"{m.label(n)}.{f}" for n, f in roots) or m.t("none")
            sys.exit(m.t("no_calls_from", flow=requested, options=options))
        return match[0]
    preferred = [k for k in roots if m.nodes[k[0]].get("role") == "viewmodel"] or roots
    return max(preferred, key=lambda k: len(trace(m, calls, *k, {k})), default=None)


def sequence_diagram(m, requested):
    calls = call_index(m)
    root = pick_flow(m, calls, requested)
    if root is None:
        return None, None
    steps = trace(m, calls, *root, {root})
    ids = Ids()
    order = [root[0]] + [dst for _, dst, _ in steps]
    lines = ["sequenceDiagram"]
    lines += [f'    participant {ids(i)} as {quote(m.label(i))}' for i in dict.fromkeys(order)]
    lines.append(f"    Note over {ids(root[0])}: {root[1]}()")
    for src, dst, function in steps:
        if function is None:
            lines.append(f"    {ids(src)}-->>{ids(dst)}: {m.t('implemented_by')}")
        else:
            lines.append(f"    {ids(src)}->>{ids(dst)}: {function}()")
    return mermaid(lines), f"{m.label(root[0])}.{root[1]}"


# ---------- 4. modules ----------

def module_usage(m):
    """Modules declared in Gradle, modules this one uses in code, and modules that use it."""
    declared = {d["path"]: d["configuration"] for d in m.module.get("dependencies", {}).get("modules", [])
                if "test" not in d["configuration"].lower()}
    used, used_by = set(), set()
    for e in m.edges:
        if e["from"] in m.nodes and e["to"] in m.externals:
            used.add(m.externals[e["to"]].get("module"))
        elif e["from"] in m.externals and e["to"] in m.nodes:
            used_by.add(m.externals[e["from"]].get("module"))
    return declared, used - {None, m.path}, used_by - {None, m.path}


def module_diagram(m):
    declared, _, used_by = module_usage(m)
    ids = Ids()
    lines = ["graph LR", f'    {ids(m.path)}["{m.path}"]']
    lines += [f'    {ids(mod)}["{mod}"] -->|{m.t("uses")}| {ids(m.path)}' for mod in sorted(used_by)]
    lines += [f'    {ids(m.path)} -->|{conf}| {ids(mod)}["{mod}"]' for mod, conf in sorted(declared.items())]
    return mermaid(lines)


# ---------- 5. Hilt ----------

def hilt_diagram(m):
    provides = [e for e in m.edges if e["kind"] == "provides"]
    injects = [e for e in m.edges
               if e["kind"] == "depends_on" and any(d.get("di") for d in e.get("details", []))]
    if not provides and not injects:
        return None
    available = {e["to"] for e in provides} | {
        node_id for node_id, node in m.nodes.items()
        if any(a.split("(")[0].rsplit(".", 1)[-1] == "Inject" for a in node.get("constructor_annotations", []))}
    outside = sorted({e["to"] for e in injects} - available)
    ids = Ids()

    def declare(node_id):
        marks = [a for a in m.nodes.get(node_id, {}).get("annotations", []) if a.startswith(HILT_ENTRY_POINTS)]
        suffix = "".join(f"<br/>@{mark}" for mark in marks)
        return f'{ids(node_id)}["{quote(m.label(node_id))}{suffix}"]'

    lines = ["graph LR"]
    involved = dict.fromkeys(end for e in provides + injects for end in (e["from"], e["to"]))
    lines += [f"    {declare(i)}" for i in involved if i not in outside]
    if outside:
        lines.append(f'    subgraph outside["{m.t("provided_outside")}"]')
        lines += [f"        {declare(i)}" for i in outside]
        lines.append("    end")
    for e in provides:
        details = e.get("details") or [{}]
        binding = details[0].get("binding", "provides").capitalize()
        lines.append(f'    {ids(e["from"])} -->|"@{binding}"| {ids(e["to"])}')
        if binding == "Binds":
            for implementation in sorted(m.implementers.get(e["to"], [])):
                if implementation not in involved:
                    lines.append(f"    {declare(implementation)}")
                lines.append(f'    {ids(e["to"])} -.{m.t("implemented_by")}.-> {ids(implementation)}')
    lines += [f'    {ids(e["from"])} -->|{m.t("injects")}| {ids(e["to"])}' for e in injects]
    return mermaid(lines)


# ---------- analysis ----------

def class_table(m):
    """Every type of the module (nested ones included) and its composables, with layer, role and KDoc."""
    listed = [i for i, n in m.nodes.items()
              if n["kind"] in ("class", "interface", "object", "enum", "annotation") or n.get("role") == "composable"]
    listed.sort(key=lambda i: (LAYERS.index(m.layers[i]), m.nodes[i]["file"], m.nodes[i].get("lines", [0])[0]))
    rows = []
    for node_id in listed:
        node = m.nodes[node_id]
        kind = " ".join([mod for mod in ("sealed", "abstract", "data", "value") if mod in node.get("modifiers", [])]
                        + [node["kind"]])
        doc = node.get("doc", "").split("\n\n")[0].replace("\n", " ") or m.t("no_kdoc")
        rows.append((m.label(node_id), kind, m.layers[node_id], node.get("role", "-"), doc))
    return table(columns(m, "classes_columns"), rows) if rows else ""


def confidence_note(m, edge):
    """Confidence note for edges that come only from codegraph; empty when the AST backs them."""
    if edge["provenance"] != ["codegraph"]:
        return ""
    values = [d["confidence"] for d in edge.get("details", []) if "confidence" in d]
    return m.t("confidence_unverified", value=min(values)) if values and min(values) < 0.9 else ""


def layer_violations(m):
    rows = []
    for e in m.edges:
        if e["from"] in m.nodes and e["to"] in m.nodes:
            pair = (m.layers[e["from"]], m.layers[e["to"]])
            if pair in m.rules.forbidden:
                rows.append((f"{m.label(e['from'])} → {m.label(e['to'])}", " → ".join(pair), e["kind"],
                             m.where(e), confidence_note(m, e) or "-"))
    if rows:
        return table(columns(m, "violations_columns"), sorted(rows))
    if not m.rules.forbidden:
        return m.t("violations_no_rules")
    return m.t("violations_none", rules=", ".join(f"{a} → {b}" for a, b in sorted(m.rules.forbidden)))


def entry_points(m):
    parts = []
    components = m.module.get("manifest", {}).get("components", [])
    if components:
        exported = {True: m.t("exported_yes"), False: m.t("exported_no")}
        rows = [(c["type"], c["class"], exported.get(c.get("exported"), m.t("exported_unset"))) for c in components]
        parts.append(m.t("manifest_components") + "\n\n" + table(columns(m, "manifest_columns"), rows))
    inbound = sorted({(m.label(e["to"]), m.label(e["from"]), m.externals[e["from"]].get("module", "?"), e["kind"])
                      for e in m.edges if e["from"] in m.externals and e["to"] in m.nodes})
    if inbound:
        parts.append(m.t("used_from_modules") + "\n\n" + table(columns(m, "used_from_columns"), inbound))
    return "\n\n".join(parts)


def external_dependencies(m):
    users = defaultdict(set)
    for e in m.edges:
        if e["from"] in m.nodes and e["to"] in m.externals:
            users[e["to"]].add(m.label(e["from"]))
    rows = []
    for ext_id, who in users.items():
        ext = m.externals[ext_id]
        origin = ext.get("module") or m.t("library")
        rows.append((0 if ext.get("module") else 1, origin, ext_id, ", ".join(sorted(who))))
    parts = []
    if rows:
        parts.append(table(columns(m, "external_columns"), [r[1:] for r in sorted(rows)]))
    declared, used, _ = module_usage(m)
    unused, undeclared = sorted(set(declared) - used), sorted(used - set(declared))
    if unused:
        parts.append(m.t("declared_unused", modules=", ".join(unused)))
    if undeclared:
        parts.append(m.t("used_undeclared", modules=", ".join(undeclared)))
    return "\n\n".join(parts)


def edges_to_verify(m):
    items = []
    for e in m.edges:
        arrow = f"{m.label(e['from'])} → {m.label(e['to'])}"
        for d in e.get("details", []):
            if "corrected_from" in d:
                basis = m.t("basis_import") if d.get("resolved_by") == "ast_import" else m.t("basis_receiver")
                items.append(m.t("review_corrected", arrow=arrow, source=d["from"], target=d["to"], where=m.where(e),
                                 proposed=m.label(d["corrected_from"]), basis=basis))
            elif e["provenance"] == ["codegraph"] and d.get("confidence", 1) < 0.9:
                items.append(m.t("review_confidence", confidence=d["confidence"], arrow=arrow, kind=e["kind"],
                                 source=d["from"], target=d["to"], where=m.where(e)))
    items += [m.t("review_warning", warning=w) for w in m.data.get("warnings", [])]
    items += [m.t("review_no_ast", label=m.label(i), file=n["file"])
              for i, n in m.nodes.items() if n.get("source") == "codegraph"]
    connected = {end for e in m.edges for end in (e["from"], e["to"])}
    items += [m.t("review_gap", label=m.label(i), role=n["role"])
              for i, n in m.nodes.items() if n.get("role") and i not in connected]
    discarded = m.data.get("sources", {}).get("codegraph", {}).get("edges_discarded")
    if discarded:
        items.append(m.t("review_discarded", count=discarded))
    codegraph = m.data.get("sources", {}).get("codegraph", {}).get("status")
    if codegraph != "ok":
        items.append(m.t("review_no_codegraph", status=codegraph))
    return "\n".join(f"- {item}" for item in dict.fromkeys(items))


# ---------- document ----------
# Every section returns (blocks, has content). An empty section keeps its heading and says why,
# and has content = False lets --only skip documents that would have nothing in them.

def titled(heading, content, empty):
    return ([heading, content], True) if content else ([heading, empty], False)


def section_layers(m, flow):
    diagram, count = layered_diagram(m)
    legend = m.t("layers_by_package", count=count, limit=m.rules.readable_limit) \
        if count > m.rules.readable_limit else m.t("layers_legend")
    return [m.t("layers_heading"), legend, diagram], count > 0


def section_flows(m, flow):
    diagram, count = flow_diagram(m)
    if not diagram:
        return titled(m.t("flows_heading"), None, m.t("flows_empty"))
    blocks = [m.t("flows_heading"), m.t("flows_legend"), diagram]
    if count > m.rules.readable_limit:
        blocks.append(m.t("flows_large", count=count))
    return blocks, True


def section_sequence(m, flow):
    diagram, name = sequence_diagram(m, flow)
    if not diagram:
        return titled(m.t("sequence_heading"), None, m.t("sequence_empty"))
    return [m.t("sequence_heading_named", name=name), diagram, m.t("sequence_note")], True


def section_modules(m, flow):
    declared, _, used_by = module_usage(m)
    return [m.t("modules_heading"), m.t("modules_legend"), module_diagram(m)], bool(declared or used_by)


# key for --only -> function that builds the section. The order is the document's order.
SECTIONS = {
    "layers": section_layers,
    "flows": section_flows,
    "sequence": section_sequence,
    "modules": section_modules,
    "hilt": lambda m, flow: titled(m.t("hilt_heading"), hilt_diagram(m), m.t("hilt_empty")),
    "classes": lambda m, flow: titled(m.t("classes_heading"), class_table(m), m.t("classes_empty")),
    "violations": lambda m, flow: ([m.t("violations_heading"), layer_violations(m)], True),
    "entries": lambda m, flow: titled(m.t("entries_heading"), entry_points(m), m.t("entries_empty")),
    "external": lambda m, flow: titled(m.t("external_heading"), external_dependencies(m), m.t("external_empty")),
    "review": lambda m, flow: titled(m.t("review_heading"), edges_to_verify(m), m.t("review_empty")),
}


def build(m, flow, selected):
    """-> (document, True if at least one of the selected sections has something to show)."""
    blocks = [m.t("doc_title", path=m.path),
              m.t("doc_intro", date=m.data.get("generated_at", "?"), nodes=len(m.nodes), edges=len(m.edges))]
    has_content = False
    for key, section in SECTIONS.items():
        if key in selected:
            section_blocks, filled = section(m, flow)
            blocks += section_blocks
            has_content = has_content or filled
    return "\n\n".join(blocks) + "\n", has_content


# ---------- index of several modules ----------

def common_group(paths):
    """Longest Gradle path shared by all modules, never a whole module path: used to shorten names."""
    split = [path.split(":") for path in paths]
    prefix = []
    for parts in zip(*split):
        if len(set(parts)) > 1:
            break
        prefix.append(parts[0])
    while prefix and any(len(parts) <= len(prefix) for parts in split):
        prefix.pop()
    return ":".join(prefix)


def build_index(maps, documents, title, lang=DEFAULT_LANG):
    """Index: only which module depends on which according to Gradle. Details live in each module's document."""
    mapped = {m.path for m in maps}
    group = common_group(sorted(mapped))
    ids = Ids()
    groups = defaultdict(list)  # parent path -> modules, to group those that share a prefix
    for m in maps:
        groups[m.path.rpartition(":")[0]].append(m.path)
    lines = ["graph LR"]
    for parent, members in sorted(groups.items()):
        if parent and len(members) > 1:
            lines.append(f'    subgraph {ids("grupo " + parent)}["{parent}"]')
            lines += [f'        {ids(path)}["{path[len(parent):]}"]' for path in members]
            lines.append("    end")
        else:
            lines += [f'    {ids(path)}["{path}"]' for path in members]

    rows = []
    for m, document in zip(maps, documents):
        declared = module_usage(m)[0]
        inside = {target: conf for target, conf in declared.items() if target in mapped and target != m.path}
        for target, configuration in sorted(inside.items()):
            label = "" if configuration == "implementation" else f"|{configuration}|"
            lines.append(f"    {ids(m.path)} -->{label} {ids(target)}")
        rows.append((f"[{m.path[len(group):]}]({document})", m.module.get("type", "-"), len(m.nodes),
                     ", ".join(target[len(group):] for target in sorted(inside)) or "-",
                     len(declared) - len(inside)))

    sections = [
        tr(lang, "index_title", title=title),
        tr(lang, "index_intro", count=len(maps)),
        tr(lang, "modules_heading"),
        tr(lang, "index_legend"),
        mermaid(lines),
        tr(lang, "index_modules_heading"),
        (tr(lang, "index_relative", group=group) if group else "") + tr(lang, "index_links"),
        table(tr(lang, "index_columns").split("|"), rows),
    ]
    return "\n\n".join(sections) + "\n"


def find_maps(path):
    """Maps for a path: the file itself, the maps in a directory, or those in docs/architecture of the modules below."""
    if path.is_file():
        return [path]
    flat = sorted(path.glob("*.module-map.json"))
    if flat:
        return flat
    return sorted(found for found in path.rglob(f"{DOCS_DIR}/*.module-map.json")
                  if "build" not in found.relative_to(path).parts)


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
    cli.add_argument("map", type=Path, nargs="?", help=tr(lang, "help_map"))
    cli.add_argument("-o", "--output", type=Path, help=tr(lang, "help_output"))
    cli.add_argument("--module", metavar="NAME", help=tr(lang, "help_module"))
    cli.add_argument("--only", metavar="SECTIONS", help=tr(lang, "help_only", sections=", ".join(SECTIONS)))
    cli.add_argument("--flow", help=tr(lang, "help_flow"))
    cli.add_argument("--sections", action="store_true", help=tr(lang, "help_sections"))
    cli.add_argument("--lang", choices=LANGS, help=tr(lang, "help_lang"))
    cli.add_argument("--config", type=Path, help=tr(lang, "help_config"))
    return cli, cli.parse_args()


def main():
    cli, args = parse_args()
    lang = requested_lang(args.lang) or DEFAULT_LANG
    if args.config and not args.config.is_file():
        sys.exit(tr(lang, "config_not_found", path=args.config))
    start = (args.map or Path.cwd()).resolve()
    config_path = args.config or find_config(start if start.is_dir() else start.parent)
    config = read_config(config_path, lang) if config_path else {}
    lang = requested_lang(args.lang) or config.get("lang") or DEFAULT_LANG
    if lang not in LANGS:
        sys.exit(tr(DEFAULT_LANG, "bad_lang", value=lang, langs=", ".join(LANGS)))
    rules = rules_from_config(config, config_path, lang) if config else DEFAULT_RULES

    if args.sections:
        print("\n".join(f"{key:18} {tr(lang, 'section_' + key)}" for key in SECTIONS))
        return
    if args.map is None:
        cli.error(tr(lang, "missing_map"))
    if not args.map.exists():
        sys.exit(tr(lang, "not_found", path=args.map))
    selected = [k.strip() for k in args.only.split(",")] if args.only else list(SECTIONS)
    unknown = [k for k in selected if k not in SECTIONS]
    if unknown:
        sys.exit(tr(lang, "unknown_sections", unknown=", ".join(unknown), available=", ".join(SECTIONS)))

    paths = find_maps(args.map)
    if not paths:
        sys.exit(tr(lang, "no_maps", path=args.map))
    maps = [Map(json.loads(path.read_text(encoding="utf-8")), rules, lang) for path in paths]
    everything = not args.module
    if args.module:
        wanted = ":" + args.module.strip(":")
        chosen = [(m, path) for m, path in zip(maps, paths) if m.path == wanted or m.path.endswith(wanted)]
        if not chosen:
            sys.exit(tr(lang, "no_module_map", module=args.module, available=", ".join(m.path for m in maps)))
        maps, paths = [m for m, _ in chosen], [path for _, path in chosen]
    if args.flow and len(maps) > 1:
        sys.exit(tr(lang, "flow_single_module"))
    if args.output and args.output.suffix == ".md" and len(maps) > 1:
        sys.exit(tr(lang, "output_must_be_dir", count=len(maps)))

    def document(path):
        """Output file for a map: next to it unless -o says otherwise. A partial document gets its own
        name so it never overwrites the full one."""
        if args.output and args.output.suffix == ".md":
            return args.output
        suffix = "." + "-".join(selected) if args.only else ""
        name = path.name.replace(".module-map.json", "") + suffix + ".md"
        return (args.output or path.parent) / name

    written, skipped = [], []
    for m, path in zip(maps, paths):
        text, has_content = build(m, args.flow, selected)
        if args.only and not has_content:
            skipped.append(m.path)
            continue
        document(path).parent.mkdir(parents=True, exist_ok=True)
        document(path).write_text(text, encoding="utf-8")
        written.append(document(path))
    if everything and not args.only and len(maps) > 1:
        # Maps in one directory: the index goes there. Maps spread over modules: in the directory given.
        together = len({path.parent for path in paths}) == 1
        index = (args.output or (paths[0].parent if together else args.map / DOCS_DIR)) / "index.md"
        index.parent.mkdir(parents=True, exist_ok=True)
        links = [Path(os.path.relpath(document(path), index.parent)).as_posix() for path in paths]
        title = maps[0].data.get("project", {}).get("name") or args.map.resolve().name
        index.write_text(build_index(maps, links, title, lang), encoding="utf-8")
        written.append(index)
    for output in written:
        print(tr(lang, "generated", path=output), file=sys.stderr)
    if skipped:
        print(tr(lang, "skipped", sections=", ".join(selected), modules=", ".join(skipped)), file=sys.stderr)


if __name__ == "__main__":
    main()
