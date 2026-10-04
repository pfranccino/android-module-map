---
name: module-map
description: Map, diagram and analyze one Kotlin/Android Gradle module, or every module inside a directory, with two bundled deterministic scripts. module_map.py builds a JSON map of the module (tree-sitter AST + codegraph index + Gradle/Manifest); module_diagrams.py turns that map into a Markdown document with five Mermaid diagrams (layered architecture, flows from the ViewModels, sequence of a flow, module dependencies, Hilt dependency injection) and an analysis (classes, layer violations, entry points, external dependencies, edges to review); for a directory it also writes an index with the dependency graph between the modules. Use this whenever the user asks to diagram, map, document or analyze the architecture of an Android/Kotlin module or feature, asks how a module is structured or what depends on what, or wants Mermaid diagrams of a module, even if they do not mention this skill or the scripts by name.
---

# Module map

Two scripts do the work, and neither needs a model: the same code always produces the same
document. Your job is to run them, and then add the two things a script cannot do: check the
doubtful edges against the source, and describe classes that have no KDoc.

Do not draw or redraw the diagrams yourself. They are generated from the map so that every
arrow is backed by an edge; a hand-drawn arrow breaks that guarantee.

## Step 1: Generate the map

1. **Find the target directory.** A Gradle path such as `:document-signer` or `:feature:login`
   maps to `document-signer/` or `feature/login/`. Confirm it contains `build.gradle.kts` or
   `build.gradle`; if not, look in `settings.gradle(.kts)`, and ask if it is still unclear.
   The target can also be a directory that holds several modules (for example `features/`, or
   the project root): the scripts then handle every module inside it.

2. **Python.** Only Python 3.10+ is required. On its first run `module_map.py` creates its own
   environment in `~/.cache/module-map/venv`, installs its two dependencies there and re-runs
   itself inside it, so do not create a virtual environment or install anything by hand. That
   first run needs network access and takes a few seconds longer.

3. **codegraph.** The map reads `.codegraph/codegraph.db` at the project root.
   - Index exists: nothing to do, the script refreshes it.
   - No index, `codegraph` on the PATH: run `codegraph init` at the project root first.
   - Not installed: add `--no-codegraph` and tell the user. The map will have structure but no
     calls, so the flow and sequence diagrams will be empty.

4. **Run.** Both scripts are in `scripts/` next to this file, and both take just the directory.

   ```
   python3 <this-skill-dir>/scripts/module_map.py <directory>
   ```

   Each map is written inside its own module, in `<module>/docs/architecture/`. Progress goes to
   stderr; a normal run takes seconds. Do not pass `--android-cli` unless the user asks for build
   metadata: it runs Gradle for minutes and the diagrams do not use it.

5. **Language.** Output is in Spanish unless `.module-map.toml` or `MODULE_MAP_LANG` says
   otherwise. If the user writes in English or asks for English, pass `--lang en` to both
   scripts; use the same language for both.

If a script fails, show the full error and stop. Do not patch it or work around it without the
user's approval.

## Step 2: Generate the documents

```
python3 <this-skill-dir>/scripts/module_diagrams.py <directory>
```

It finds the maps under that directory and writes each `<module>.md` next to its map. With
several modules it also writes `<directory>/docs/architecture/index.md`, which holds only the
graph of Gradle dependencies between those modules; dependencies on other modules and
class-level detail stay in each module's document.

Options:

- `--only layers,flows` generates only those sections. The keys are `layers`, `flows`,
  `sequence`, `modules`, `hilt`, `classes`, `violations`, `entries`, `external` and `review`;
  `--sections` prints them with a description. A partial document goes to
  `<module>.<sections>.md`, so it never overwrites the full one, and a module with nothing to
  show for those sections gets no file.
- `--module <name>` restricts the run to one module of the directory, for example
  `--module engine --only classes`.
- `--flow Class.function` picks the flow of the sequence diagram for one module. The default is
  the ViewModel function with the longest chain of calls; an unknown flow lists the available
  ones.
- `-o <directory>` on either script collects the output in one directory instead of inside each
  module.

For the review in Step 3, go module by module. With more than five modules, ask the user which
ones to review first instead of reviewing all of them.

## Step 3: Review what the scripts cannot decide

Write the results to `<module>.review.md` next to the generated document. The generated
document is overwritten on every run, so do not edit it.

1. **Edges to review.** Go through the last section of the generated document.
   - Low-confidence edges come from codegraph, which resolves calls by name. Open the file at
     the given line and state whether the call really goes to that target.
   - Corrected edges were already fixed by the script, using the declared type of the receiver
     or the import in the calling file. Confirm the correction in the source.
   - Discarded edges are only counted: codegraph proposed them by name and nothing in the calling
     file supported the target. Mention the count; there is nothing to verify.
   - A "possible gap" is a class with a role but no relationships. Check whether it is reached
     through something the map cannot see (navigation routes, reflection, generated code).
2. **Layer violations.** For each row marked as unverified, confirm it in the source before
   calling it a violation.
3. **Responsibilities.** For each class marked "(sin KDoc)" or "(no KDoc)", read it and write one
   sentence on what it is responsible for.

Report only what you checked, with `file:line` for each finding. If the review shows that a
diagram is wrong, say which edge is wrong and why; do not fix the diagram by hand.

## When a diagram or rule should change

How layers are assigned (package segment, then role), which layer crossings count as
violations, and the size above which the layer diagram is drawn by package can be set in
`.module-map.toml` at the project root (`[layers.by_segment]`, `[layers.by_role]`,
`layers.forbidden`, `layers.readable_limit`; see the README). If the user wants different
behaviour, propose the change to that file and wait for approval, so the result stays
reproducible. Propose a change to `scripts/module_diagrams.py` only for what the file cannot
express, such as which nodes are drawn.

## Finish

Tell the user where the three files are (map, document, review), how many nodes and edges the
map has, and which review items need a human decision.

## Constraints

- Do not modify the project's source code.
- Do not modify the scripts on your own.
- Do not commit anything.
