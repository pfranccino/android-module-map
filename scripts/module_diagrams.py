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
       python module_diagrams.py <map.json | directory_of_maps> [-o output]

Given a module (or a directory of modules) it reads the maps that module_map.py left in each
<module>/docs/architecture/ and writes every document next to its map. With several modules it
also writes an index.md with the graph of Gradle dependencies between them. --module restricts it to one module. With --only the result
goes to <module>.<sections>.md, so the full document is never overwritten, and modules that have
nothing for those sections get no file.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

LAYERS = ["Presentation", "Domain", "Data", "DI", "Other"]
LAYER_BY_SEGMENT = {"ui": "Presentation", "presentation": "Presentation", "domain": "Domain",
                    "data": "Data", "di": "DI"}
LAYER_BY_ROLE = {"activity": "Presentation", "fragment": "Presentation", "composable": "Presentation",
                 "viewmodel": "Presentation", "usecase": "Domain", "datasource": "Data",
                 "api_service": "Data", "dao": "Data", "entity": "Data", "database": "Data",
                 "di_module": "DI"}
# (source layer, target layer) pairs that break the dependency rule. DI is exempt.
FORBIDDEN = {("Presentation", "Data"), ("Domain", "Presentation"), ("Domain", "Data"),
             ("Data", "Presentation")}
HILT_ENTRY_POINTS = ("HiltViewModel", "AndroidEntryPoint")
DOCS_DIR = "docs/architecture"  # where module_map.py leaves each map by default, inside its module
READABLE_LIMIT = 40  # node count above which the layer diagram stops being readable


class Map:
    def __init__(self, data):
        self.data = data
        self.module = data.get("module", {})
        self.path = self.module.get("path") or self.module.get("dir", "módulo")
        self.nodes = {n["id"]: n for n in data.get("nodes", [])}
        self.externals = {n["id"]: n for n in data.get("external_nodes", [])}
        self.edges = data.get("edges", [])
        self.implementers = defaultdict(list)  # interface -> implementations inside the module
        for e in self.edges:
            if e["kind"] == "implements" and e["from"] in self.nodes:
                self.implementers[e["to"]].append(e["from"])
        self.layers = {node_id: self.layer(node) for node_id, node in self.nodes.items()}

    def layer(self, node):
        """Layer by package segment (the one closest to the class) and, failing that, by role."""
        packages = [s for s in node["id"].split(".")[:-1] if s[:1].islower()]
        for segment in reversed(packages):
            if segment in LAYER_BY_SEGMENT:
                return LAYER_BY_SEGMENT[segment]
        if node.get("role") == "repository":
            return "Domain" if node["kind"] == "interface" else "Data"
        return LAYER_BY_ROLE.get(node.get("role"), "Other")

    def label(self, node_id):
        node = self.nodes.get(node_id) or self.externals.get(node_id) or {"name": node_id}
        parent = node.get("parent")
        return f"{self.label(parent)}.{node['name']}" if parent else node["name"]

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


def mermaid(lines):
    return "```mermaid\n" + "\n".join(lines) + "\n```"


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
        return "El mapa no tiene nodos con rol ni dependencias entre clases que dibujar.", 0
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
            arrows.append(f"    {ids(dst)} -.implementado por.-> {ids(src)}")
        elif kind == "extends":
            arrows.append(f"    {ids(src)} -.extiende.-> {ids(dst)}")
        elif kind == "instantiates":
            arrows.append(f"    {ids(src)} -->|crea| {ids(dst)}")
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
    groups = (("ext_modules", "Otros módulos", "project"), ("ext_libraries", "Librerías", "library"))
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
    return mermaid(lines), len(shown)


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

    arrows, order, pending = {}, list(roots), list(roots)  # (source, target) -> ViewModel functions
    while pending:
        node = pending.pop(0)
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
            options = ", ".join(f"{m.label(n)}.{f}" for n, f in roots) or "ninguno"
            sys.exit(f"No hay llamadas registradas desde {requested}. Flujos disponibles: {options}")
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
            lines.append(f"    {ids(src)}-->>{ids(dst)}: implementado por")
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
    lines += [f'    {ids(mod)}["{mod}"] -->|usa| {ids(m.path)}' for mod in sorted(used_by)]
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
        lines.append('    subgraph outside["Provisto fuera de este módulo"]')
        lines += [f"        {declare(i)}" for i in outside]
        lines.append("    end")
    for e in provides:
        binding = e.get("details", [{}])[0].get("binding", "provides").capitalize()
        lines.append(f'    {ids(e["from"])} -->|"@{binding}"| {ids(e["to"])}')
        if binding == "Binds":
            for implementation in sorted(m.implementers.get(e["to"], [])):
                if implementation not in involved:
                    lines.append(f"    {declare(implementation)}")
                lines.append(f'    {ids(e["to"])} -.implementado por.-> {ids(implementation)}')
    lines += [f'    {ids(e["from"])} -->|inyecta| {ids(e["to"])}' for e in injects]
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
        doc = node.get("doc", "").split("\n\n")[0].replace("\n", " ") or "(sin KDoc)"
        rows.append((m.label(node_id), kind, m.layers[node_id], node.get("role", "-"), doc))
    return table(["Clase", "Tipo", "Capa", "Rol", "Responsabilidad (KDoc)"], rows) if rows else ""


def confidence_note(edge):
    """Confidence note for edges that come only from codegraph; empty when the AST backs them."""
    if edge["provenance"] != ["codegraph"]:
        return ""
    values = [d["confidence"] for d in edge.get("details", []) if "confidence" in d]
    return f"confianza {min(values)}, sin verificar" if values and min(values) < 0.9 else ""


def layer_violations(m):
    rows = []
    for e in m.edges:
        if e["from"] in m.nodes and e["to"] in m.nodes:
            pair = (m.layers[e["from"]], m.layers[e["to"]])
            if pair in FORBIDDEN:
                rows.append((f"{m.label(e['from'])} → {m.label(e['to'])}", " → ".join(pair), e["kind"],
                             m.where(e), confidence_note(e) or "-"))
    if not rows:
        return ("No se encontraron violaciones. Se revisaron todas las aristas entre capas con estas reglas: "
                "Presentation no depende de Data, Domain no depende de Presentation ni de Data, "
                "Data no depende de Presentation. La capa DI está exenta.")
    return table(["Arista", "Capas", "Tipo", "Evidencia", "Nota"], sorted(rows))


def entry_points(m):
    parts = []
    components = m.module.get("manifest", {}).get("components", [])
    if components:
        rows = [(c["type"], c["class"], {True: "sí", False: "no"}.get(c.get("exported"), "sin declarar"))
                for c in components]
        parts.append("**Componentes del Manifest**\n\n" + table(["Tipo", "Clase", "Exported"], rows))
    inbound = sorted({(m.label(e["to"]), m.label(e["from"]), m.externals[e["from"]].get("module", "?"), e["kind"])
                      for e in m.edges if e["from"] in m.externals and e["to"] in m.nodes})
    if inbound:
        parts.append("**Usado desde otros módulos**\n\n"
                     + table(["Elemento de este módulo", "Lo usa", "Módulo", "Tipo"], inbound))
    return "\n\n".join(parts)


def external_dependencies(m):
    users = defaultdict(set)
    for e in m.edges:
        if e["from"] in m.nodes and e["to"] in m.externals:
            users[e["to"]].add(m.label(e["from"]))
    rows = []
    for ext_id, who in users.items():
        ext = m.externals[ext_id]
        origin = ext.get("module") or "librería"
        rows.append((0 if ext.get("module") else 1, origin, ext_id, ", ".join(sorted(who))))
    parts = []
    if rows:
        parts.append(table(["Origen", "Tipo externo", "Usado por"], [r[1:] for r in sorted(rows)]))
    declared, used, _ = module_usage(m)
    unused, undeclared = sorted(set(declared) - used), sorted(used - set(declared))
    if unused:
        parts.append("Declarados en Gradle sin uso observado en el código Kotlin (el mapa no ve recursos ni "
                     "código generado, así que no implica que sobren): " + ", ".join(unused) + ".")
    if undeclared:
        parts.append("Usados en código sin estar declarados directamente en este módulo (probable dependencia "
                     "transitiva): " + ", ".join(undeclared) + ".")
    return "\n\n".join(parts)


def edges_to_verify(m):
    items = []
    for e in m.edges:
        arrow = f"{m.label(e['from'])} → {m.label(e['to'])}"
        for d in e.get("details", []):
            if "corrected_from" in d:
                basis = "el import del archivo" if d.get("resolved_by") == "ast_import" else \
                    "el tipo declarado del receptor"
                items.append(f"Corregida: `{arrow}` ({d['from']} → {d['to']}, {m.where(e)}). codegraph proponía "
                             f"`{m.label(d['corrected_from'])}`; se usó {basis}.")
            elif e["provenance"] == ["codegraph"] and d.get("confidence", 1) < 0.9:
                items.append(f"Confianza {d['confidence']}: `{arrow}` ({e['kind']}: {d['from']} → {d['to']}, "
                             f"{m.where(e)}). Resuelta por nombre; revisar en el código.")
    items += [f"Aviso del mapa: {w}" for w in m.data.get("warnings", [])]
    items += [f"Sin AST (solo datos de codegraph): `{m.label(i)}` en {n['file']}."
              for i, n in m.nodes.items() if n.get("source") == "codegraph"]
    connected = {end for e in m.edges for end in (e["from"], e["to"])}
    items += [f"Posible vacío: `{m.label(i)}` tiene rol {n['role']} pero ninguna relación en el mapa."
              for i, n in m.nodes.items() if n.get("role") and i not in connected]
    discarded = m.data.get("sources", {}).get("codegraph", {}).get("edges_discarded")
    if discarded:
        items.append(f"codegraph: aristas descartadas: {discarded}. Nada en el archivo que llama respaldaba "
                     "el destino (ni tipo declarado, ni import, ni mismo paquete).")
    codegraph = m.data.get("sources", {}).get("codegraph", {}).get("status")
    if codegraph != "ok":
        items.append(f"codegraph no se usó ({codegraph}): el mapa no tiene llamadas, instanciaciones ni "
                     "referencias, solo la estructura leída del AST.")
    return "\n".join(f"- {item}" for item in dict.fromkeys(items))


# ---------- document ----------
# Every section returns (blocks, has content). An empty section keeps its heading and says why,
# and has content = False lets --only skip documents that would have nothing in them.

def titled(heading, content, empty):
    return ([heading, content], True) if content else ([heading, empty], False)


def section_layers(m, flow):
    diagram, count = layered_diagram(m)
    blocks = ["## Arquitectura por capas",
              "Flecha sólida: depende de o llama a. Flecha punteada: relación de tipos.", diagram]
    if count > READABLE_LIMIT:
        blocks.append(f"El diagrama tiene {count} nodos; por encima de {READABLE_LIMIT} conviene mapear "
                      "por paquete (pasando a module_map.py el directorio del paquete).")
    return blocks, count > 0


def section_flows(m, flow):
    diagram, count = flow_diagram(m)
    if not diagram:
        return titled("## Flujos desde los ViewModels", None,
                      "El mapa no registra llamadas fiables desde un ViewModel de este módulo.")
    blocks = ["## Flujos desde los ViewModels",
              "La etiqueta de cada flecha que sale de un ViewModel es la función que inicia la llamada. "
              "Cada implementación se dibuja como su interfaz. Solo llamadas dentro del módulo que el "
              "AST confirmó por el tipo del receptor o que codegraph resolvió con confianza de 0.9 o más.",
              diagram]
    if count > READABLE_LIMIT:
        blocks.append(f"El diagrama tiene {count} nodos; para verlo por partes, mapea un paquete.")
    return blocks, True


def section_sequence(m, flow):
    diagram, name = sequence_diagram(m, flow)
    if not diagram:
        return titled("## Secuencia", None,
                      "El mapa no tiene llamadas (`calls`) que salgan de este módulo, así que no hay flujo que "
                      "dibujar. Las llamadas vienen de codegraph: revisa en «Aristas a revisar» si se usó.")
    return [f"## Secuencia: {name}", diagram,
            "Solo llamadas entre clases, en orden de línea. No refleja ramas, bucles ni asincronía; "
            "otro flujo con `--flow Clase.funcion`."], True


def section_modules(m, flow):
    declared, _, used_by = module_usage(m)
    return ["## Dependencias entre módulos",
            "Salientes: declaradas en Gradle. Entrantes (`usa`): usos observados en el código.",
            module_diagram(m)], bool(declared or used_by)


# key for --only -> (what it is, function that builds the section). The order is the document's order.
SECTIONS = {
    "layers": ("diagrama de arquitectura por capas", section_layers),
    "flows": ("diagrama de flujos desde los ViewModels", section_flows),
    "sequence": ("diagrama de secuencia de un flujo", section_sequence),
    "modules": ("diagrama de dependencias del módulo", section_modules),
    "hilt": ("diagrama de inyección de dependencias", lambda m, flow: titled(
        "## Inyección de dependencias", hilt_diagram(m),
        "El mapa no registra `@Provides`, `@Binds` ni dependencias con `@Inject`.")),
    "classes": ("tabla de todas las clases con tipo, capa, rol y KDoc", lambda m, flow: titled(
        "## Clases", class_table(m), "El mapa no tiene clases.")),
    "violations": ("violaciones de capas", lambda m, flow: (["## Violaciones de capas", layer_violations(m)], True)),
    "entries": ("puntos de entrada", lambda m, flow: titled(
        "## Puntos de entrada", entry_points(m),
        "El mapa no registra componentes en el Manifest ni usos desde otros módulos.")),
    "external": ("dependencias externas", lambda m, flow: titled(
        "## Dependencias externas", external_dependencies(m), "El mapa no registra dependencias externas.")),
    "review": ("aristas a revisar", lambda m, flow: titled(
        "## Aristas a revisar", edges_to_verify(m), "Nada que revisar.")),
}


def build(m, flow, selected):
    """-> (document, True if at least one of the selected sections has something to show)."""
    blocks = [f"# {m.path}: arquitectura",
              f"Generado por `module_diagrams.py` a partir del mapa del {m.data.get('generated_at', '?')}. "
              f"{len(m.nodes)} nodos y {len(m.edges)} aristas. No editar a mano: se regenera desde el mapa."]
    has_content = False
    for key, (_, section) in SECTIONS.items():
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


def build_index(maps, documents, title):
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
        f"# {title}: módulos",
        f"Generado por `module_diagrams.py` a partir de {len(maps)} mapas. No editar a mano.",
        "## Dependencias entre módulos",
        "Solo dependencias declaradas en Gradle entre los módulos de este grupo. Una flecha sin etiqueta "
        "es `implementation`. Las dependencias hacia otros módulos, los usos en el código y el detalle "
        "por clase están en el documento de cada módulo.",
        mermaid(lines),
        "## Módulos",
        (f"Nombres relativos a `{group}`. " if group else "") + "Cada módulo enlaza a su documento.",
        table(["Módulo", "Tipo", "Nodos", "Depende de (en este grupo)", "Otros módulos"], rows),
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


def main():
    cli = argparse.ArgumentParser(description="Diagramas Mermaid y análisis a partir del mapa de module_map.py.")
    cli.add_argument("map", type=Path, nargs="?",
                     help="directorio del módulo (o de varios módulos), directorio de mapas, o un mapa JSON")
    cli.add_argument("-o", "--output", type=Path,
                     help="directorio de salida (por defecto, junto a cada mapa); con un solo módulo también "
                          "puede ser el archivo .md")
    cli.add_argument("--module", metavar="NAME",
                     help="con varios módulos, procesa solo ese (por ejemplo engine o :core:engine)")
    cli.add_argument("--only", metavar="SECTIONS",
                     help="secciones a generar, separadas por coma (por defecto, todas): " + ", ".join(SECTIONS)
                          + ". Se escriben en <modulo>.<secciones>.md, sin tocar el documento completo")
    cli.add_argument("--flow", help="flujo del diagrama de secuencia, como LoginViewModel.submit (un solo módulo)")
    cli.add_argument("--sections", action="store_true", help="lista las secciones disponibles y termina")
    args = cli.parse_args()

    if args.sections:
        print("\n".join(f"{key:18} {description}" for key, (description, _) in SECTIONS.items()))
        return
    if args.map is None:
        cli.error("falta el módulo, el directorio o el mapa")
    if not args.map.exists():
        sys.exit(f"No existe {args.map}")
    selected = [k.strip() for k in args.only.split(",")] if args.only else list(SECTIONS)
    unknown = [k for k in selected if k not in SECTIONS]
    if unknown:
        sys.exit(f"Sección desconocida: {', '.join(unknown)}. Disponibles: {', '.join(SECTIONS)}")

    paths = find_maps(args.map)
    if not paths:
        sys.exit(f"No hay mapas en {args.map}. Este script los lee, no los crea: ejecuta antes "
                 f"module_map.py {args.map}")
    maps = [Map(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    everything = not args.module
    if args.module:
        wanted = ":" + args.module.strip(":")
        chosen = [(m, path) for m, path in zip(maps, paths) if m.path == wanted or m.path.endswith(wanted)]
        if not chosen:
            sys.exit(f"Ningún mapa es del módulo {args.module}. Disponibles: {', '.join(m.path for m in maps)}")
        maps, paths = [m for m, _ in chosen], [path for _, path in chosen]
    if args.flow and len(maps) > 1:
        sys.exit("--flow solo aplica a un módulo: pasa ese módulo o usa --module")
    if args.output and args.output.suffix == ".md" and len(maps) > 1:
        sys.exit(f"Hay {len(maps)} módulos: -o debe ser un directorio, o usa --module para elegir uno")

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
        index.write_text(build_index(maps, links, title), encoding="utf-8")
        written.append(index)
    for output in written:
        print(f"generado: {output}", file=sys.stderr)
    if skipped:
        print(f"sin contenido para {', '.join(selected)}, no se generó documento: {', '.join(skipped)}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
