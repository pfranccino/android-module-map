<div align="center">

# 🏗️ android-module-map

**Turn your Kotlin/Android modules into architecture diagrams — automatically.**

[![CI](https://github.com/pfranccino/android-module-map/actions/workflows/ci.yml/badge.svg)](https://github.com/pfranccino/android-module-map/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Two scripts. No Gradle build. No AI model. Same input → same output, every time.

[Quick Start](#-quick-start) · [Features](#-features) · [Configuration](#%EF%B8%8F-configuration) · [How It Works](#-how-it-works) · [Examples](examples/)

</div>

---

Understanding how an Android module is wired — which layers talk to which, where
Hilt bindings land, what calls what — usually means reading every file or drawing
diagrams by hand. **This tool reads the code and draws them for you.**

```
module_map.py        source code  →  .module-map.json
module_diagrams.py   map JSON     →  .md  (Mermaid diagrams + analysis)
```

<details>
<summary><b>📊 See a generated diagram</b> (click to expand)</summary>

<br>

> Generated from the synthetic project in [`examples/`](examples/) with `--lang en`

```mermaid
graph TD
    subgraph Presentation
        n2["LoginActivity"]
        n3["LoginScreen"]
        n4["LoginForm"]
        n5["LoginViewModel"]
    end
    subgraph Domain
        n0["AuthRepository"]
        n6["LoginUseCase"]
    end
    subgraph Data
        n7["AuthApi"]
        n1["AuthRepositoryImpl"]
        n8["SessionStore"]
    end
    subgraph DI
        n9["LoginModule"]
    end
    subgraph ext_modules["Other modules"]
        n10["ApiClient<br/>:core:network"]
    end
    n1 --> n10
    n1 --> n7
    n1 --> n8
    n2 --> n3
    n3 --> n4
    n3 --> n5
    n5 --> n6
    n6 --> n0
    n9 --> n10
    n0 -.implemented by.-> n1
    classDef layerPresentation fill:#dbeafe,stroke:#2563eb,color:#0f172a
    class n2,n3,n4,n5 layerPresentation
    classDef layerDomain fill:#dcfce7,stroke:#16a34a,color:#0f172a
    class n0,n6 layerDomain
    classDef layerData fill:#fef3c7,stroke:#d97706,color:#0f172a
    class n7,n1,n8 layerData
    classDef layerDI fill:#f3e8ff,stroke:#9333ea,color:#0f172a
    class n9 layerDI
    classDef external fill:#ffffff,stroke:#94a3b8,stroke-dasharray:4,color:#334155
    class n10 external
```

</details>

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 🧱 | **Layered architecture** | Groups classes into Presentation, Domain, Data, and DI by package, annotation, and role — one color per layer |
| 🔀 | **Flow diagrams** | Traces ViewModel → use case → repository → API with AST-verified receivers |
| 📐 | **Sequence diagrams** | Step-by-step call sequence for any ViewModel function |
| 💉 | **Hilt/Dagger graph** | Maps `@Provides`, `@Binds`, `@Inject` across the module |
| 🚨 | **Violation detection** | Flags forbidden layer dependencies with `file:line` evidence |
| 🔗 | **Cross-module tracking** | Matches Gradle declarations against actual code usage |
| 📚 | **Version catalogs** | Resolves `libs.*` notations from `gradle/libs.versions.toml` to real coordinates |
| 🗺️ | **Scales to big modules** | Above 40 drawn nodes the layer diagram switches to one box per package |
| 🌐 | **English or Spanish** | `--lang en` or `--lang es` for documents, messages and the map legend |
| ⚙️ | **Configurable rules** | Layer names and forbidden dependencies live in `.module-map.toml`, not in the code |
| ♻️ | **Deterministic** | Same code → same output. Works in CI, code review, or as a Claude Code skill |

---

## 🚀 Quick Start

> **Requires Python 3.10+** · Works on Linux, macOS, and Windows

**Option A — install the commands** (recommended):

```bash
pipx install git+https://github.com/pfranccino/android-module-map

# from the root of your Android project
module-map feature/login
module-diagrams feature/login
```

**Option B — run the scripts from a clone**, with nothing installed:

```bash
python3 path/to/scripts/module_map.py feature/login
python3 path/to/scripts/module_diagrams.py feature/login
```

On Windows, use `python` instead of `python3`. In this mode `module_map.py` creates its own venv in
`~/.cache/module-map/venv` the first time it runs and installs `tree-sitter` there (needs network
once).

Output is in Spanish by default. Add `--lang en` to both commands (or set `MODULE_MAP_LANG=en`, or
`lang = "en"` in [`.module-map.toml`](#%EF%B8%8F-configuration)) to get it in English.

<details>
<summary><b>See expected output</b></summary>

```
[module_map +  0.0s] :feature:login: parsing 6 files...
[module_map +  0.1s] feature/login/docs/architecture/feature-login.module-map.json: 19 nodes, 5 externals, 29 edges, 0 warnings
```

```
generated: feature/login/docs/architecture/feature-login.md
```

```
feature/login/docs/architecture/
├── feature-login.module-map.json   ← structured map (nodes + edges)
├── feature-login.md                ← full document with all diagrams
└── feature-login.classes.md        ← only when --only is used
```

</details>

### Multiple modules

```bash
module-map features
module-diagrams features
```

Generates an `index.md` with the Gradle dependency graph across all modules.

### Single section

```bash
module-diagrams features --module login --only classes
```

---

## 📄 What You Get

Each document can include these sections — all generated by default, `--only` picks a subset:

| Key | Section | What it shows |
|:---:|---|---|
| `layers` | Architecture diagram | Classes grouped by layer with dependency arrows (packages for large modules) |
| `flows` | Flow diagram | ViewModel → use case → repository → API chains |
| `sequence` | Sequence diagram | Step-by-step call trace for one flow |
| `modules` | Module graph | Gradle dependencies + cross-module usage |
| `hilt` | DI diagram | Providers, bindings, and injection consumers |
| `classes` | Class table | Every type with kind, layer, role, and KDoc |
| `violations` | Layer violations | Forbidden dependencies with file:line evidence |
| `entries` | Entry points | Manifest components + usages from other modules |
| `external` | External deps | Dependencies by module and by library |
| `review` | Review edges | Corrections, low-confidence links, warnings |

> Subsets are written to `<module>.<sections>.md` — the full document is never overwritten.

---

## 📋 Requirements

| Dependency | Required | Notes |
|---|:---:|---|
| **Python 3.10+** | ✅ | `pipx` installs `tree-sitter`; in script mode `module_map.py` installs it in its own venv |
| **[codegraph](https://www.npmjs.com/package/@colbymchenry/codegraph)** | ❌ | Adds calls, instantiations, references. Run `codegraph init` once. Without it: no flow/sequence diagrams |
| **Android CLI** | ❌ | `--android-cli` attaches Gradle build metadata. Slow (runs Gradle) |

---

## ⚙️ Options

<details>
<summary><b>module-map</b> / <code>module_map.py</code> <code>&lt;dir&gt;</code></summary>

`<dir>` is a module, a package inside a module, or a directory containing several modules.

| Option | Meaning |
|---|---|
| `-o, --output` | Collect maps in this directory (or a `.json` file for one module) |
| `--no-codegraph` | Skip the codegraph index |
| `--android-cli` | Run `android describe` and attach build metadata |
| `--lang es\|en` | Language of the messages and of the legend inside the map |
| `--config` | Path to a `.module-map.toml` (looked up automatically otherwise) |

</details>

<details>
<summary><b>module-diagrams</b> / <code>module_diagrams.py</code> <code>&lt;path&gt;</code></summary>

`<path>` is a module, a directory of modules, a directory of maps, or one map JSON.

| Option | Meaning |
|---|---|
| `--only layers,flows` | Generate only these sections |
| `--module engine` | Process only this module from a multi-module directory |
| `--flow Class.function` | Pick the flow for the sequence diagram |
| `--sections` | List available section keys and exit |
| `-o, --output` | Output directory (or `.md` file for one module) |
| `--lang es\|en` | Language of the document and the messages |
| `--config` | Path to a `.module-map.toml` (looked up automatically otherwise) |

</details>

---

## 🛠️ Configuration

Put a `.module-map.toml` at the root of your Android project (next to `settings.gradle.kts`). Both
commands find it by walking up from the path you give them. Every key is optional. The quickest
start is to copy [`.module-map.example.toml`](.module-map.example.toml), which holds the defaults
with comments, and edit what you need. An example with overrides:

```toml
lang = "en"                  # es | en

[layers]
readable_limit = 60          # above this many nodes, the layer diagram is drawn by package
forbidden = [                # replaces the default rules entirely
    ["Presentation", "Data"],
    ["Domain", "Presentation"],
    ["Domain", "Data"],
]

[layers.by_segment]          # merged over the defaults: package segment -> layer
feature = "Presentation"
api = "Other"                # a default (api -> Data) can be overridden too

[layers.by_role]             # merged over the defaults: role -> layer
mapper = "Data"
```

Layers are `Presentation`, `Domain`, `Data`, `DI` and `Other`. An unknown key or layer stops the
run with a message instead of being ignored. Language precedence: `--lang`, then
`MODULE_MAP_LANG`, then `lang` in the file, then Spanish.

---

## 🔍 How It Works

`module_map.py` combines three sources into a single JSON map:

```mermaid
graph LR
    A["🌳 AST<br/><i>tree-sitter-kotlin</i>"] --> D["📦 module-map.json"]
    B["📊 codegraph<br/><i>.codegraph/codegraph.db</i>"] --> D
    C["📝 Gradle + Manifest<br/><i>build.gradle.kts, libs.versions.toml</i>"] --> D
    D --> E["📄 Mermaid diagrams<br/><i>module_diagrams.py</i>"]
```

| Source | What it provides |
|---|---|
| **AST** | Declarations, signatures, KDoc, annotations, inheritance, Hilt bindings, roles |
| **codegraph** | Calls, instantiations, references (including cross-module) |
| **Gradle + Manifest** | Module type, dependencies (with `libs.*` resolved through the version catalog), components — no Gradle execution needed |

<details>
<summary><b>Edge validation</b> — how false positives are filtered</summary>

<br>

codegraph resolves by name, so `isAuthorized()` can match an unrelated namesake. Each edge is
validated against the calling file:

1. **Receiver type** — `repo.load()` resolves via the declared type of `repo`
2. **Import match** — the import overrides codegraph's target when they differ
3. **Target import** — an import of the target class itself
4. **Same package** — target is in the caller's package or covered by a star import

No match → edge discarded. **Missing arrow > false arrow.**

</details>

<details>
<summary><b>JSON map structure</b></summary>

<br>

One node or edge per line, so `grep` works on it.

| Key | Content |
|---|---|
| `module` | Gradle path, type, namespace, plugins, dependencies (`resolved` coordinates for `libs.*`), Manifest |
| `nodes` | Declarations: FQN, kind, role, file, lines, KDoc, constructor, functions |
| `external_nodes` | Symbols outside the module (`project` or `library`) |
| `edges` | Typed relationships with `provenance`, `weight`, and `details` |
| `legend` | Meaning of each edge kind and provenance, in the chosen language |

</details>

<details>
<summary><b>Layer assignment</b></summary>

<br>

Layers are assigned by package segment (`ui` → Presentation, `domain` → Domain, `data` → Data,
`di` → DI), then by role, then by import heuristics, then by the layer of their neighbors.
Change the mapping in [`.module-map.toml`](#%EF%B8%8F-configuration).

</details>

---

## 🧪 Testing

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]"      # Windows: .venv\Scripts\pip
.venv/bin/pytest                          # Windows: .venv\Scripts\pytest
```

The suite covers the AST map, the codegraph edge validation (against a hand-built index), version
catalogs, configuration, both languages, the CLI end to end, and checks that the committed
[`examples/`](examples/) match what the script generates. CI runs it on Linux, macOS and Windows
with Python 3.10 and 3.13, plus a run of the scripts with nothing installed.

---

## 🤖 Claude Code Skill

Use this as a [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code) by cloning
into your project's skills directory:

```bash
git clone https://github.com/pfranccino/android-module-map.git .claude/skills/module-map
```

The skill runs both scripts, then goes further: it verifies edges listed under "review" against
the source and describes classes missing KDoc.

---

## ⚠️ Limitations

> These are known boundaries — not bugs.

- **Syntactic only** — Kotlin-inferred types are invisible to the AST
- **Kotlin only** — Java classes appear via codegraph with minimal info
- **No test sources** — test code is excluded by design
- **Calls need codegraph** — without it, flow and sequence diagrams are empty
- **Gradle via regex** — dependencies added by convention plugins are not seen; only the default
  `gradle/libs.versions.toml` catalog is read
- **Not extracted** — navigation routes, UI state transitions, `Flow` collectors
- **Two languages** — Spanish (default) and English

---

## 🤝 Contributing

Contributions welcome! Please open an issue before submitting large changes. `main` is protected:
changes go through a pull request, and CI must pass.

```bash
git clone https://github.com/pfranccino/android-module-map.git
cd android-module-map
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

After changing `module_diagrams.py`, regenerate the examples so their test keeps passing:

```bash
python scripts/module_diagrams.py examples
```

---

<div align="center">

**[MIT License](LICENSE)**

Made for Android developers who'd rather read diagrams than grep through modules.

</div>
