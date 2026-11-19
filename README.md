# aindex — an offline Go project index for AI coding agents

`aindex` walks a Go project and writes a small, hierarchical index
(`.ai-index/`) that an AI coding agent (or a human in a hurry) can read instead
of the whole tree: one Markdown overview, one page per package, a symbol records
file, a file records file and a machine-readable stats file.

* **Offline and dependency-free.** Pure Python 3.11+ standard library. Nothing is
  installed, nothing is executed and no Go toolchain, `go list` or `gopls` is
  needed — the Go source is lexed and parsed by `aindex` itself.
* **Byte-accurate.** Every record carries the byte offsets and line range of its
  declaration, so an agent can jump straight to `file.go:120-148`.
* **Honest about what it knows.** A call is recorded only when it resolves to
  exactly one indexed symbol; everything dynamic (interface dispatch, function
  values, other modules) ends up in `unresolved_calls` instead of being guessed.
* **Deterministic.** Same tree in, same index out (bar `generated_at`, which
  honours `SOURCE_DATE_EPOCH`).

## Install

There is nothing to install. Either run the script from a checkout:

```sh
python3 index_go_project.py /path/to/my-go-project
```

or use the package directly:

```sh
python3 -m aindex /path/to/my-go-project --clean
```

## Usage

```sh
# index a sibling project: the index lands in ../my-go-project/.ai-index
python3 index_go_project.py ../my-go-project

# index the current directory into a custom directory and print JSON
python3 index_go_project.py . -o /tmp/go-index --json

# rebuild from scratch, skipping test files and generated code
python3 index_go_project.py . --clean --no-tests --ignore '*.gen.go'

# machine-friendly: no output at all, fail when a file did not parse
python3 index_go_project.py . --quiet --fail-on-parse-error
```

A successful run prints where the index went, the statistics and the file to
read first:

```
indexed /home/me/my-go-project -> /home/me/my-go-project/.ai-index
  packages: 12
  files: 84
  symbols: 640
  calls: 1180 (resolved 1102, unresolved 78)
  parse errors: 0
  duration: 0.34s
  start here: /home/me/my-go-project/.ai-index/PROJECT.md
```

With `--json` the same information is printed as a JSON object (the fields of
`stats.json`, plus `output_dir` and `index_files`).

### Options

| Option | Meaning |
| --- | --- |
| `root` | project root to index (default: the current directory) |
| `-o DIR`, `--out DIR` | output directory; a relative value is relative to `ROOT` (default `.ai-index`) |
| `--clean` | delete the output directory before writing the new index |
| `--ignore PATTERN` | extra `.gitignore`-style pattern to exclude; repeatable |
| `--no-gitignore` | ignore the project's own `.gitignore` |
| `--no-tests` | skip `*_test.go` files |
| `--max-doc-chars N` | truncate stored doc comments to `N` characters (default `1200`) |
| `--quiet` | print nothing but errors |
| `--json` | print the statistics as JSON instead of the summary |
| `--fail-on-parse-error` | exit with status `1` when any file could not be parsed |
| `--version`, `--help` | usual |

Exit status is `0` on success and `1` on a bad invocation (unknown directory,
output directory equal to the project root, or a parse error when
`--fail-on-parse-error` was given).

## What the index looks like

```
.ai-index/
├── PROJECT.md              # overview: summary, package table, entry points, how to grep
├── packages/
│   ├── root.md             # one page per package: files, imports, every symbol
│   └── internal_store.md
├── symbols.jsonl           # one JSON object per symbol
├── files.jsonl             # one JSON object per Go file
└── stats.json              # the numbers shown by the CLI
```

`PROJECT.md` is the entry point: it lists the packages (with a link to each
page) and the exported entry points, and it repeats the `grep` one-liners that
work on `symbols.jsonl`. The package pages group symbols by kind (structs,
interfaces, funcs, methods, consts, vars) and show signatures, docs, fields,
methods, calls and callers with line ranges.

Every line of `symbols.jsonl` is one symbol — a package, a type, a struct, an
interface, a func, a method, a const, a var or a test — with the fields below
in this order:

| Field | Meaning |
| --- | --- |
| `id` | stable id, e.g. `func:example.com/demo/internal/store:New` |
| `kind` | `package`, `struct`, `interface`, `type`, `alias`, `func`, `test`, `method`, `const`, `var` |
| `package`, `package_path` | package name and its import path |
| `file` | path relative to the project root (`""` for the package record) |
| `name`, `qualified_name` | bare and package-qualified name |
| `receiver`, `receiver_type` | method receiver, and its base type for generics |
| `signature` | the declaration without the body |
| `doc` | attached doc comment, truncated to `--max-doc-chars` |
| `exported` | `true` when the name is exported |
| `start_line`, `end_line` | 1-based, inclusive line range |
| `start_byte`, `end_byte` | byte offsets into the file |
| `calls`, `callers` | symbol ids that resolve to exactly one indexed symbol |
| `unresolved_calls` | names that could not be resolved (prefixed with `?`) |
| `types` | types used in the signature |
| `fields` | field names (structs) |
| `methods` | method names (interfaces) |
| `imports` | import paths of the file |
| `test_related` | the file or package is test code |
| `content_hash` | hash of the declaration text |

`files.jsonl` holds one record per Go file: `file`, `package`, `package_path`,
`lines`, `symbols`, `imports`, `build_tags`, `test_related`, `parse_error`,
`error` and `content_hash`. Files that could not be parsed are still listed,
with `parse_error: true` and the message in `error`; a file without a package
clause is reported as `missing package clause`.

## Using it as a library

The CLI is a thin wrapper around the pipeline, so the same index can be built
without touching the command line:

```python
from aindex import IndexOptions, build_index, run

# build in memory: useful for tests and other tools
result = build_index(IndexOptions(root="/path/to/my-go-project", include_tests=False))
print(result.stats.summary_lines())
for package in result.iter_packages():
    print(package.path, len(package.symbols))

# build and write .ai-index/
result, written = run(IndexOptions(root="/path/to/my-go-project", clean=True))
print("\n".join(written))
```

Useful entry points:

| Name | Purpose |
| --- | --- |
| `IndexOptions(root=..., output_dir=..., max_doc_chars=..., extra_ignores=(...), use_gitignore=..., include_tests=..., clean=...)` | everything the CLI can configure |
| `build_index(options)` | read, parse and link; writes nothing |
| `write_index(result, index_dir)` | serialize an `IndexResult`; returns the paths written |
| `run(options)` | `build_index` + `write_index`, i.e. what the CLI calls |
| `IndexResult` | `root`, `module`, `packages`, `symbols`, `files`, `stats`, plus `iter_packages()` / `production_packages()` / `to_dict()` / `to_json()` |
| `SymbolRecord`, `FileRecord`, `PackageInfo`, `Stats` | the dataclasses behind the JSON files |

## How it works

| Module | Responsibility |
| --- | --- |
| `aindex.discovery` | walk the tree, honour `.gitignore`, read `go.mod`, find packages |
| `aindex.golex` | dependency-free Go lexer with exact byte offsets and automatic semicolon insertion |
| `aindex.goparse` | declaration and symbol extraction on top of the lexer |
| `aindex.symbols` | stable ids, doc comments, signatures, fields, method sets |
| `aindex.calls` | approximate direct call/caller graph |
| `aindex.model` | the index data model (dataclasses) |
| `aindex.render` | Markdown rendering |
| `aindex.serialize` | JSONL / stats serialization |
| `aindex.pipeline` | one run: discover, parse, link, write |
| `aindex.cli` | command line interface |

Notes on behaviour worth knowing:

* `vendor/`, `testdata/`, build output directories (`build`, `dist`, `bin`), VCS
  metadata and every dot directory are skipped, as is the output directory
  itself (which is why `-o` inside the project root does not index itself).
* `go.mod` supplies the module path and the Go version; package paths in the
  index are import paths (`example.com/demo/internal/store`), not directories.
* Files constrained by build tags (`foo_linux.go`, `foo_windows.go`) that
  declare the same symbol get a `#file` suffix on their ids so ids stay unique.
* Test files get `test_related: true`; `Test*`/`Benchmark*`/`Fuzz*`/`Example*`
  functions declared in a `*_test.go` file have kind `test` instead of `func`.
* A file that does not parse is still indexed as far as it got; the record keeps
  `parse_error` and `error`, and the run continues.

## Tests

The suite is plain `unittest` and standard library only:

```sh
python3 -m unittest discover -s tests -t .
```

It builds a small but realistic Go project in a temporary directory (a `main`
package, an internal package pair with an interface, a generic type, an
external test package, build-tagged files, a file with a syntax error and a file
without a package clause) and checks discovery, lexing, parsing, symbol linking,
rendering, serializing and the CLI itself — including running both entry points
as subprocesses. Nothing outside the temporary directory is touched.
