"""Markdown rendering of the index.

Two kinds of pages are produced:

``PROJECT.md``
    The orientation page: what the project is, how many packages, files and
    symbols it has, the package map and the most likely entry points.  Read by
    an agent first, to decide *where* to look.

``packages/<slug>.md``
    One page per package: files, imports, and every symbol with its signature,
    documentation and relationships.

Style rules that matter for an AI reader:

* identifiers are always backticked, and relationship targets are rendered as
  the id used in ``symbols.jsonl`` (bare names for the current package, full
  ``kind:path:Name`` ids otherwise), so anything printed here can be grepped;
* signatures are copied verbatim from the source and fenced with ``go``;
* documentation is quoted, never paraphrased, and truncated with ``...``.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping

from .constants import (
    KIND_ALIAS,
    KIND_CONST,
    KIND_FIELD,
    KIND_FUNC,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_STRUCT,
    KIND_TEST,
    KIND_TYPE,
    KIND_VAR,
    PACKAGES_DIRNAME,
    PROJECT_FILENAME,
    SYMBOL_KIND_ORDER,
)
from .model import FileRecord, IndexResult, PackageInfo, SymbolRecord
from .serialize import ensure_directory, write_text

#: Rows shown in the project page before it is truncated.
MAX_PROJECT_ENTRIES = 40

_KIND_TITLES: dict[str, str] = {
    KIND_STRUCT: "Structs",
    KIND_INTERFACE: "Interfaces",
    KIND_TYPE: "Types",
    KIND_ALIAS: "Aliases",
    KIND_FUNC: "Functions",
    KIND_TEST: "Tests",
    KIND_METHOD: "Methods",
    KIND_CONST: "Constants",
    KIND_VAR: "Variables",
    KIND_FIELD: "Fields",
    KIND_PACKAGE: "Packages",
}

_KIND_LABELS: dict[str, str] = {
    KIND_STRUCT: "struct",
    KIND_INTERFACE: "interface",
    KIND_TYPE: "type",
    KIND_ALIAS: "alias",
    KIND_FUNC: "func",
    KIND_TEST: "test",
    KIND_METHOD: "method",
    KIND_CONST: "const",
    KIND_VAR: "var",
    KIND_FIELD: "field",
    KIND_PACKAGE: "package",
}


def _label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def _plural(kind: str) -> str:
    return _KIND_TITLES.get(kind, kind.capitalize() + "s")


def _slug(directory: str, package_name: str) -> str:
    """Filesystem friendly page name for a package directory."""
    stem = directory.replace("/", "_").replace("\\", "_") or "root"
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in stem)
    if package_name.endswith("_test"):
        cleaned += "-test"
    return cleaned.strip("-.") or "root"


def package_pages(packages: Iterable[PackageInfo]) -> dict[str, str]:
    """Map every package key to its page path relative to the index directory.

    Two packages in the same directory (``foo`` and ``foo_test``) and
    directory names that differ only by separators are told apart by a numeric
    suffix, so the mapping is always unique.
    """
    pages: dict[str, str] = {}
    used: dict[str, int] = {}
    for package in sorted(packages, key=lambda item: item.key):
        stem = _slug(package.directory, package.name)
        count = used.get(stem, 0) + 1
        used[stem] = count
        if count > 1:
            stem = f"{stem}-{count}"
        pages[package.key] = f"{PACKAGES_DIRNAME}/{stem}.md"
    return pages


def _shorten(target_id: str, package_path: str) -> str:
    """Render a reference: bare name inside the package, full id outside it."""
    marker = f":{package_path}:"
    if marker in target_id:
        return target_id.split(marker, 1)[1]
    return target_id


def _doc_block(doc: str) -> list[str]:
    """Quote a doc comment (Go style: verbatim, one ``>`` per line)."""
    if not doc:
        return []
    return [f"> {line}".rstrip() for line in doc.splitlines()] + [""]


def _code_block(text: str, language: str = "go") -> list[str]:
    if not text:
        return []
    return [f"```{language}", text.rstrip(), "```", ""]


def _relationship_lines(symbol: SymbolRecord, package_path: str) -> list[str]:
    """Bullet list of everything the symbol is connected to."""
    lines: list[str] = []
    if symbol.fields:
        label = "method signatures" if symbol.kind == KIND_INTERFACE else "fields"
        lines.append(f"- **{label}**: " + "; ".join(f"`{field}`" for field in symbol.fields))
    if symbol.methods:
        lines.append(
            "- **methods**: "
            + ", ".join(f"`{_shorten(method, package_path)}`" for method in symbol.methods)
        )
    for label, key, transform in (
        ("uses types", "types", True),
        ("calls", "calls", True),
        ("called by", "callers", True),
    ):
        values = getattr(symbol, key)
        if not values:
            continue
        rendered = [
            f"`{_shorten(value, package_path) if transform else value}`" for value in values
        ]
        lines.append(f"- **{label}**: " + ", ".join(rendered))
    if symbol.unresolved_calls:
        unresolved = ", ".join(f"`?{name}`" for name in symbol.unresolved_calls)
        lines.append(f"- **unresolved calls**: {unresolved}")
    return lines


def _symbol_lines(symbol: SymbolRecord, package_path: str) -> list[str]:
    """One symbol section: heading, signature, doc, relationships."""
    lines = [f"### `{symbol.qualified_name}`", ""]
    lines.extend(_code_block(symbol.signature))
    meta = [f"*{_label(symbol.kind)}*"]
    if symbol.file:
        meta.append(f"`{symbol.file}` lines {symbol.start_line}-{symbol.end_line}")
    meta.append(f"id `{symbol.id}`")
    if not symbol.exported:
        meta.append("unexported")
    lines.append(" · ".join(meta))
    lines.append("")
    lines.extend(_doc_block(symbol.doc))
    relationships = _relationship_lines(symbol, package_path)
    if relationships:
        lines.extend(relationships)
        lines.append("")
    return lines



def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_package(
    package: PackageInfo,
    file_records: Mapping[str, FileRecord] | None = None,
) -> str:
    """Render one package page."""
    lines = [f"# Package `{package.name}`", ""]
    facts = [f"path `{package.path}`"]
    if package.directory:
        facts.append(f"dir `{package.directory}`")
    facts.append(f"{len(package.files)} file(s)")
    facts.append(f"{len(package.symbols)} symbol(s)")
    if package.is_test_package:
        facts.append("test package")
    lines.append(" · ".join(facts))
    lines.append("")
    lines.extend(_doc_block(package.doc))

    records = file_records or {}
    per_file: dict[str, int] = {}
    for symbol in package.symbols:
        per_file[symbol.file] = per_file.get(symbol.file, 0) + 1
    tagged = {path: records[path].build_tags for path in package.files if path in records}
    with_tags = any(tagged.get(path) for path in package.files)
    header = "| File | Lines | Symbols | Build tags |" if with_tags else "| File | Lines | Symbols |"
    rule = "| --- | --- | --- | --- |" if with_tags else "| --- | --- | --- |"
    lines.extend(["## Files", "", header, rule])
    for rel_path in package.files:
        record = records.get(rel_path)
        line_count = record.lines if record is not None else ""
        row = f"| `{rel_path}` | {line_count} | {per_file.get(rel_path, 0)} |"
        if with_tags:
            row += f" {_cell('; '.join(tagged.get(rel_path, [])))} |"
        lines.append(row)
    lines.append("")

    imports = sorted({path for symbol in package.symbols for path in symbol.imports})
    if imports:
        lines.extend(["## Imports", ""])
        lines.extend(f"- `{path}`" for path in imports)
        lines.append("")

    grouped: dict[str, list[SymbolRecord]] = {}
    for symbol in package.sorted_symbols():
        grouped.setdefault(symbol.kind, []).append(symbol)
    for kind in SYMBOL_KIND_ORDER:
        symbols = grouped.get(kind)
        if not symbols:
            continue
        lines.extend([f"## {_plural(kind)}", ""])
        for symbol in symbols:
            lines.extend(_symbol_lines(symbol, package.path))
    lines.append(
        "*Bare names above are symbols of this package; "
        "full ids look like `kind:package-path:Name` and can be grepped in `symbols.jsonl`.*"
    )
    lines.append("")
    return "\n".join(lines)



def _counts_table(result: IndexResult) -> list[str]:
    stats = result.stats
    packages = len(result.packages)
    tests = sum(1 for package in result.packages.values() if package.is_test_package)
    rows = [
        ("Packages", f"{packages} ({tests} test)"),
        ("Go files", str(stats.files)),
        ("Symbols", str(stats.symbols)),
        ("Calls", f"{stats.calls} ({stats.resolved_calls} resolved, {stats.unresolved_calls} unresolved)"),
        ("Parse errors", str(stats.parse_errors)),
        ("Ignored files", str(stats.ignored_files)),
        ("Duration", f"{stats.duration_seconds:.2f}s"),
    ]
    lines = ["| Metric | Value |", "| --- | --- |"]
    lines.extend(f"| {label} | {_cell(value)} |" for label, value in rows)
    return lines


def _package_table(result: IndexResult, pages: Mapping[str, str]) -> list[str]:
    lines = ["| Package | Dir | Files | Symbols | Page |", "| --- | --- | --- | --- | --- |"]
    for package in result.iter_packages():
        page = pages.get(package.key, "")
        label = f"`{package.name}`"
        if package.is_test_package:
            label += " *(test)*"
        link = f"[{os.path.basename(page)}]({page})" if page else ""
        lines.append(
            f"| {label} | `{package.directory or '.'}` | {len(package.files)} | "
            f"{len(package.symbols)} | {link} |"
        )
    return lines


def _entry_point_table(
    result: IndexResult, kinds: Iterable[str], pages: Mapping[str, str]
) -> list[str]:
    """Table of exported symbols of the given kinds, capped at MAX_PROJECT_ENTRIES."""
    wanted = set(kinds)
    rows = [
        symbol
        for symbol in result.symbols
        if symbol.kind in wanted and symbol.exported and not symbol.test_related
    ]
    rows.sort(key=lambda symbol: (symbol.package_path, symbol.qualified_name.casefold()))
    lines = ["| Symbol | Package | Signature |", "| --- | --- | --- |"]
    for symbol in rows[:MAX_PROJECT_ENTRIES]:
        page = pages.get(f"{symbol.package_path}|{symbol.package}", "")
        package = f"[`{symbol.package}`]({page})" if page else f"`{symbol.package}`"
        lines.append(f"| `{symbol.qualified_name}` | {package} | `{_cell(symbol.signature)}` |")
    if len(rows) > MAX_PROJECT_ENTRIES:
        lines.append(f"| *… {len(rows) - MAX_PROJECT_ENTRIES} more* |  | see the package pages |")
    return lines


def render_project(result: IndexResult, pages: Mapping[str, str] | None = None) -> str:
    """Render the project overview page (``PROJECT.md``)."""
    index_pages = dict(pages) if pages is not None else package_pages(result.packages.values())
    module = result.module
    title = module.module_path or os.path.basename(result.root) or "Go project"
    lines = [f"# {title} — Go project index", ""]
    facts = [f"root `{result.root}`", f"generated {result.stats.generated_at}"]
    if module.go_version:
        facts.append(f"go {module.go_version}")
    facts.append(f"backend `{result.stats.backend}`")
    lines.append(" · ".join(facts))
    lines.append("")
    lines.append(
        "A symbol-level index of this Go project, built offline. Read this page first, "
        "then open the package page (or `symbols.jsonl`) for details."
    )
    lines.append("")

    lines.extend(["## Summary", ""])
    lines.extend(_counts_table(result))
    lines.append("")
    lines.extend(_index_files_section(module.module_path))
    lines.extend(["## Packages", ""])
    lines.extend(_package_table(result, index_pages))
    lines.append("")
    lines.extend(["## Entry points (exported package-level functions)", ""])
    lines.extend(_entry_point_table(result, (KIND_FUNC,), index_pages))
    lines.append("")
    lines.extend(["## Exported types", ""])
    lines.extend(
        _entry_point_table(
            result, (KIND_STRUCT, KIND_INTERFACE, KIND_TYPE, KIND_ALIAS), index_pages
        )
    )
    lines.append("")
    lines.extend(_parse_error_section(result))
    lines.extend(_notes_section())
    return "\n".join(lines)



def _index_files_section(module_path: str = "") -> list[str]:
    """Static description of the index layout plus copy-pasteable queries."""
    queries = [
        "grep '\"qualified_name\":\"New\"' symbols.jsonl   # find symbols by name",
        "grep -c '\"kind\":\"method\"' symbols.jsonl        # count methods",
    ]
    if module_path:
        queries.append(
            f"grep 'func:{module_path}/' symbols.jsonl   # functions of this module"
        )
    return [
        "## Index files",
        "",
        "| File | Content |",
        "| --- | --- |",
        "| `PROJECT.md` | this overview: summary, packages, entry points |",
        f"| `{PACKAGES_DIRNAME}/*.md` | one page per package: files, imports, every symbol |",
        "| `symbols.jsonl` | one JSON object per symbol (ids, calls, callers, types, fields) |",
        "| `files.jsonl` | one JSON object per Go file (package, imports, build tags, hash) |",
        "| `stats.json` | the numbers above, machine readable |",
        "",
        "Useful queries (run inside the index directory):",
        "",
        "```sh",
        *queries,
        "```",
        "",
    ]


def _parse_error_section(result: IndexResult) -> list[str]:
    broken = [record for record in result.files if record.parse_error]
    if not broken:
        return []
    lines = ["## Parse errors", ""]
    for record in broken[:MAX_PROJECT_ENTRIES]:
        lines.append(f"- `{record.file}`: {_cell(record.error)}")
    if len(broken) > MAX_PROJECT_ENTRIES:
        lines.append(f"- *… {len(broken) - MAX_PROJECT_ENTRIES} more*")
    lines.append("")
    return lines


def _notes_section() -> list[str]:
    return [
        "## Notes",
        "",
        "- `calls` lists only references that resolve to exactly one indexed symbol; "
        "anything dynamic (interface dispatch, function values, calls into other "
        "modules) is kept in `unresolved_calls` instead of being guessed.",
        "- Test entry points (`Test*`, `Benchmark*`, `Fuzz*`, `Example*`) have kind `test`.",
        "- `vendor/`, `testdata/`, build output, dot directories and the project's "
        "`.gitignore` are excluded from the index.",
        "",
    ]


def write_markdown(
    index_dir: str,
    result: IndexResult,
    pages: Mapping[str, str] | None = None,
) -> list[str]:
    """Write ``PROJECT.md`` and every package page; returns the paths written."""
    ensure_directory(index_dir)
    mapping = dict(pages) if pages is not None else package_pages(result.packages.values())
    file_records = {record.file: record for record in result.files}
    written = [
        write_text(os.path.join(index_dir, PROJECT_FILENAME), render_project(result, mapping))
    ]
    for package in result.iter_packages():
        path = os.path.join(index_dir, mapping[package.key])
        ensure_directory(os.path.dirname(path))
        written.append(write_text(path, render_package(package, file_records)))
    return written

