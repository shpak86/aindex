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
  Before giving up it types what it can locally, so `srv.ListenAndServe()`
  resolves when `srv` was declared as `*ProxyServer`, `ProxyServer{}`,
  `new(ProxyServer)` or by a constructor returning that type, and so does
  `s.dialer.Dial()` when `dialer` is a field of one project type — the exact
  rules and their limits are in [what it will not do](#what-it-will-not-do).
* **Deterministic.** Same tree in, same index out (bar `generated_at`, which
  honours `SOURCE_DATE_EPOCH`).
* **Queryable.** `aindex-query` reads the index back: one command per question
  (overview, one package, search, a symbol, its callers and callees, a source
  range) as text for a human or as a single JSON document for an agent, with
  exit codes worth branching on.

## Install

There is nothing to install: run the scripts from a checkout, or let `pipx`
put the two commands on your `PATH`.

### Nothing to install

Run the script from a checkout:

```sh
python3 aindex-make /path/to/my-go-project
```

or use the package directly:

```sh
python3 -m aindex /path/to/my-go-project --clean
```

Reading an existing index needs no install either — see
[`aindex-query`](#querying-it-aindex-query) below.

### With pipx

```sh
pipx install /path/to/aindex-checkout
aindex-make /path/to/my-go-project
```

`pipx` keeps the tool in an environment of its own (your system Python is left
alone), exposes `aindex-make` and `aindex-query`, and takes them away again with
`pipx uninstall aindex-go-index`. Nothing else gets installed: both packages are
Python 3.11+ standard library with no dependencies. Since `aindex` is taken on
PyPI, the *distribution* is named **`aindex-go-index`**, while the import names
and the commands stay `aindex` / `aindex_query` and `aindex-make` /
`aindex-query`; once a release is published, `pipx install aindex-go-index` is
the same thing.

Installing from a directory makes `pipx` build the project once (to learn the
distribution name), so it needs a package index or a warm pip cache. An
air-gapped machine should install a wheel instead — there is nothing left to
build for it and no dependencies to resolve:

```sh
# with a network, pip fetches the build backend itself:
python3 -m pip wheel --no-deps -w dist .
# air-gapped, with the build backend already installed system-wide:
PYTHONPATH=/usr/lib/python3/dist-packages python3 -m pip wheel --no-build-isolation --no-deps -w dist .
pipx install --force dist/aindex_go_index-*.whl
```

Re-running the install (from the wheel or from the checkout) is also the
upgrade path: `pipx` records where the tool came from.

## Usage

```sh
# index a sibling project: the index lands in ../my-go-project/.ai-index
python3 aindex-make ../my-go-project

# index the current directory into a custom directory and print JSON
python3 aindex-make . -o /tmp/go-index --json

# rebuild from scratch, skipping test files and generated code
python3 aindex-make . --clean --no-tests --ignore '*.gen.go'

# machine-friendly: no output at all, fail when a file did not parse
python3 aindex-make . --quiet --fail-on-parse-error
```

A successful run prints where the index went, the statistics and the file to
read first:

```
indexed /home/me/my-go-project -> /home/me/my-go-project/.ai-index
  packages: 5
  files: 25
  symbols: 181
  calls: 736 (resolved 198, unresolved 538)
  parse errors: 0
  duration: 0.07s
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
| `calls`, `callers` | symbol ids that resolve to exactly one indexed symbol — by name, or through the inferred type of a local (`srv.Stop()`) or of one of its project-typed fields (`s.dialer.Dial()`) |
| `unresolved_calls` | names that could not be resolved (prefixed with `?`), including every call whose receiver or field type stays unknown |
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

## Querying it: `aindex-query`

The index is easier to read than the tree, but it is still far more than most
questions need. `aindex-query` answers one question per invocation, from the
index alone — it never re-parses a `.go` file (except `read`, which prints the
source lines you asked for) and never writes anything:

```sh
# what is in here: totals, health, the package map, the exported entry points
python3 aindex-query overview

# every package, then one package in detail (name, import path or directory)
python3 aindex-query packages
python3 aindex-query package internal/store --limit 20

# rank symbols; --kind is repeatable, plus --package/--file/--exported/--no-tests
python3 aindex-query search "New store" --kind func --exported --limit 5

# one symbol in full: kind, location, signature, doc, types, calls, callers
python3 aindex-query symbol 'method:example.com/demo/internal/store:memory.Get'
python3 aindex-query symbol store.New

# who calls it, and what it calls (raw, unresolved names listed separately)
python3 aindex-query callers memory.Get
python3 aindex-query callees memory.Get

# the source of a symbol, or of a file / line range, with context lines
python3 aindex-query read memory.Get --context 2
python3 aindex-query read internal/store/store.go:12-20

# is this index complete, consistent, current and readable?
python3 aindex-query doctor
```

### Install

Nothing to install — Python 3.11+ and the standard library only:

```sh
./aindex-query --help            # straight from a checkout
python3 -m aindex_query --help   # the same thing as a module
```

`pipx install /path/to/aindex-checkout` (see [Install](#install) above) also
puts `aindex-query` on `PATH`, which is the handy way to call it from another
project's directory.

The project root comes from `--root` (default: the current directory), so the
tool works both from inside the project and from anywhere else. The index is
looked up in this order and the first hit wins:

1. `--index DIR`;
2. the `AINDEX_INDEX_DIR` environment variable;
3. `<project root>/.ai-index`.

A relative `--index` (or environment variable) is resolved against the project
root, which mirrors the indexer's `-o` convention.

### Commands

| Command | Question it answers |
| --- | --- |
| `overview` | what is indexed: totals, `health:`, the package map, the entry points |
| `packages` | every package: import path, files, symbols, whether it has tests |
| `package NAME` | one package in detail: doc, imports, files, symbols and signatures |
| `search QUERY` | which symbols match the words, ranked by score, with kind, location and signature |
| `symbol REF` | one symbol in full: kind, location, signature, doc, types, calls, callers |
| `callers REF` | which indexed symbols call it |
| `callees REF` | which ids it calls, plus the raw names that never resolved |
| `read TARGET` | source lines of a symbol, or of `file.go[:12[-34]]` |
| `doctor` | whether the index is complete, consistent, current and readable |

`REF` accepts an id (`func:example.com/demo/internal/store:New`),
`Type.Method`, `import.path.Name`, `directory.Name` or a bare name. Every
spelling has to identify exactly one symbol: anything less specific exits `3`
and prints the candidates, with the ids you can paste back. A name that matches
a method and a function never resolves by luck.

`search` is deterministic: exact matches beat prefixes, prefixes beat
occurrences in signatures, docs and paths; a multi-word query is scored per
token and scaled by coverage, and ties break exported-first, then by kind, name
and id. Nothing is fuzzy, so `search` is also the way to discover a symbol's
exact id before using it in `callers`/`callees`/`read`.

| Flag | Where | Meaning |
| --- | --- | --- |
| `--root DIR` | all | project root (default: `.`) |
| `--index DIR` | all | index directory (default: `$AINDEX_INDEX_DIR`, else `<root>/.ai-index`) |
| `--json` | all | one JSON document on stdout instead of text |
| `--limit N` | all but `packages` | cap the list; `0` means no limit, negative is a usage error |
| `--packages N` | `overview` | cap the package table (default: all) |
| `--kind KIND` | `search` | only this kind, repeatable (`func`, `method`, `struct`, …) |
| `--package PATH`, `--file PATH` | `search` | restrict to one package import path or file |
| `--exported`, `--no-tests` | `search` | only exported symbols / skip test code |
| `--start N`, `--end N` | `read` | override the range (1-based, inclusive) |
| `--context N` | `read` | also print `N` lines before and after |
| `--symbol` | `read` | read `TARGET` as a symbol even though it looks like a path |
| `--version`, `--help`, `-h` | all | usual |

The defaults are tuned for an agent's context window: `overview` lists 15 entry
points, `package` 200 symbols, `search` 10 results, `symbol` 50 candidates when
ambiguous, `callers`/`callees` 50 ids, `read` 400 lines and `doctor` 10 examples
per failing check. Every list says how many items it showed out of how many, so
a truncated answer is never mistaken for a complete one.

### For an agent

Every command is a pure function of the index, so it is safe to run in a loop
and cheap to run twice. `--json` makes the answer parseable without any prose
filtering, and the exit code says what to do next:

```sh
# the ids search found are the arguments of the next command
python3 aindex-query --json search "Store" --limit 3 | jq -r '.results[].id'
python3 aindex-query --json symbol memory.Get | jq -r '.symbol.signature, .callers[].name'

# before trusting anything, check the index itself
python3 aindex-query doctor || echo "regenerate the index"

# the only command that touches the tree: it prints the bytes you asked for
python3 aindex-query read main.go --json | jq -r '.lines[] | "\(.number)\t\(.text)"'
```

With `--json` stdout carries exactly one JSON document; with an error it carries
nothing at all and the message goes to stderr, so `jq` never gets half a
document. Two failures are still results: a reference that does not resolve
(`found: false` plus `candidates`, exit `3`) and a failing `doctor` (a full
report, exit `4`).

### Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| `0` | success (also: no command given, which prints the help) | — |
| `1` | invalid usage (unknown command or flag, negative limit, `--start 0`) | fix the command line |
| `2` | index not found (`--index`/`--root` point at nothing, or `symbols.jsonl` is absent) | run the indexer |
| `3` | symbol not found, or ambiguous | pick an id from `candidates` |
| `4` | invalid or corrupted index (`doctor` failing, unreadable `symbols.jsonl`) | regenerate the index |
| `5` | source file missing or unreadable (`read`) | the index is stale for that file |
| `6` | security: the requested path escapes the project root | stay inside the root |

### The JSON contract

`--json` before or after the command, always one document, always these keys:

| Command | Top-level keys |
| --- | --- |
| `overview` | `project_root`, `index_dir`, `explicit_index`, `generated_at`, `index_age_seconds`, `module_path`, `go_version`, `stats`, `stats_missing`, `problems`, `packages`, `entry_points` |
| `packages` | `count`, `packages`, `pages` |
| `package` | `package`, `package_doc`, `package_page`, `files`, `symbols` |
| `search` | `query`, `filters`, `limit`, `total`, `results` |
| `symbol` | `symbol`, `calls`, `callers`, `dangling_calls` |
| `callers` | `query`, `symbol`, `count`, `callers` |
| `callees` | `query`, `symbol`, `count`, `callees`, `unresolved_calls`, `unresolved_count`, `dangling_calls`, `dangling_count` |
| `read` | `file`, `path`, `symbol`, `start`, `end`, `total_lines`, `lines`, `truncated`, `index_parse_error` |
| `doctor` | `index_dir`, `project_root`, `checks`, `failures`, `warnings`, `problems` |

`limit` is the cap that was applied, not the number of matches; `total` is the
number of matches. In `read`, `file` is the path relative to the project root
and `path` the absolute one; each entry of `lines` is `{"number": 12, "text":
"..."}`; `truncated` says the `--limit` cut the range short; `index_parse_error`
says the file did not parse when it was indexed; and `start`/`end` may be
inverted (`1`/`0`) for an empty file. `symbol` is `null` when the range came
from a path rather than a symbol. A `results` entry is a symbol card (`id`,
`kind`, `package`, `package_path`, `name`, `qualified_name`, `signature`,
`doc`, `file`, `start_line`, `end_line`, `exported`, `test_related`) plus
`score`; the symbol document inside `symbol` carries the full record, including
`calls` and `unresolved_calls`, and `calls`/`callers`/`callees` entries are
cards too (edges only store ids, so `dangling_calls` may contain an id with no
card).

### Using it from Python

The package is importable, so a tool can ask the same questions without a
process per call:

```python
from aindex_query import IndexStore, resolve_config, search
from aindex_query.search import SearchFilters

store = IndexStore(resolve_config(root="/path/to/my-go-project"))
print(store.stats.packages, "packages")
for symbol in store.by_name.get("New", ()):
    print(symbol.id, symbol.signature)

filters = SearchFilters(kinds=("func", "method"), exported_only=True)
for result in search(store.symbols, "New store", filters=filters, limit=5).results:
    print(round(result.score, 2), result.symbol.id)
```

An installed copy lives inside `pipx`'s private environment, which the importing
program cannot see, so a caller should get the distribution into its own
environment: `pip install aindex-go-index` (or `pip install .` in a checkout),
while `pipx` is for the two commands.

| Module | Responsibility |
| --- | --- |
| `aindex_query.config` | locate the project root and the index directory |
| `aindex_query.index_store` | load `symbols.jsonl` / `files.jsonl`, resolve references |
| `aindex_query.search` | deterministic ranking and filters |
| `aindex_query.source_reader` | the one place a source file is opened (plus the traversal guard) |
| `aindex_query.render` | the text of every command |
| `aindex_query.cli` | argument parsing, dispatch and exit codes |
| `aindex_query.errors` | the exception types and the exit codes |

### What it will not do

* **Answer about code that changed since the index was built.** Every command
  except `read` is a pure function of `.ai-index/`. `read` prints the file as it
  is on disk now, which is exactly why it reports `index_parse_error` and the
  line counts alongside the text. `doctor` is how you find out an index is
  stale: it re-checks that every indexed source file still exists.
* **Guess the call graph.** A call is only recorded when it resolves to exactly
  one indexed symbol. Standard library and other modules, interface dispatch,
  function values and reflection end up in `unresolved_calls` and are printed as
  raw names, never attributed to a symbol; `callers` likewise cannot see callers
  outside the index.
* **Type a receiver from more than one declaration.** A local, parameter or
  receiver is typed only from its own declaration: `var srv Thing`,
  `srv := Thing{}`, `srv := &Thing{}`, `srv := new(Thing)`, or
  `srv := NewThing(...)` when `NewThing` has exactly one result that is a single
  type declared in this project. A struct field is followed one level deep, and
  only when the field itself is one plain project type: `s.dialer.Dial()`
  resolves because `Server.dialer` is a `*Dialer`. The field type is resolved in
  the file that declares the struct, so a field of another package's type works
  too, and nothing needs to be declared in any particular order. Nothing beyond
  that is followed: a value that came out of an interface, a slice or map, an
  external package or a reassignment stays unknown, so does a chain through two
  fields (`x.a.b.Method()`), and a name that two different types claim is left
  unresolved instead of picked — which is why `h.store.Get()` (dispatch through
  an interface field) and `f(item)` (a function value) keep their raw names in
  the fixture the suite builds. Inference runs while the index is built, so an
  index written by an older `aindex` keeps its old `calls`/`unresolved_calls`
  until it is regenerated.
* **Follow a broken edge.** `dangling_calls` are ids that appear in the index's
  edge lists but have no record in `symbols.jsonl` (a half-written index). They
  are reported as a warning, never followed.
* **Match fuzzily.** `search` is a fixed ranking of names, docs and paths; there
  is no stemming, no typo tolerance and no semantic search.
* **Write anything.** No file in `.ai-index/` is ever created, moved or touched,
  and `read` refuses paths outside the project root (exit `6`).

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
| `aindex.calls` | direct call/caller graph, with conservative local and field type inference |
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
rendering, serializing and the indexer CLI itself — including running both entry
points as subprocesses. `tests/test_query_cli.py` drives `aindex-query` against
a real index of that same fixture: every command, the `--json` contract (key
sets, exit codes, stdout/stderr separation), a deliberately damaged index (a
malformed JSONL line, a missing `files.jsonl`, a missing `stats.json`, a
duplicate id, a source file that vanished, an unreadable `symbols.jsonl`) and
the two entry points themselves, run as real subprocesses. Nothing outside the
temporary directory is touched.
