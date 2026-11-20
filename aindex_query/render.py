"""Human-readable output for every command (``--json`` bypasses this module).

Three rules keep the text usable by an agent:

* stdout carries **either** text **or** one JSON document, never a mix -- the
  JSON mode is handled by :mod:`aindex_query.cli` alone;
* every list is prefixed by its size and says so when it was cut short, so
  ``showing 10 of 53`` is visible rather than implied;
* anything the index does not know is printed as a labelled gap
  (``unresolved_calls``), never silently dropped or invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from .constants import (
    DOC_PREVIEW_CHARS,
    IMPORTANT_KINDS,
    KIND_ORDER,
    OVERVIEW_KEY_SYMBOLS,
    PACKAGE_SYMBOL_LIMIT,
)
from .models import Symbol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .index_store import IndexStore, PackageGroup
    from .search import SearchOutcome

#: Indentation for every nested line.
INDENT = "  "

#: Width the trait column in a search hit is padded to before the location.
FLAG_COLUMN = 34

_KIND_LABELS: dict[str, str] = {
    "struct": "struct",
    "interface": "interface",
    "type": "type",
    "alias": "alias",
    "func": "func",
    "test": "test",
    "method": "method",
    "const": "const",
    "var": "var",
    "package": "package",
}


def human_age(seconds: float | None) -> str:
    """``28m`` / ``3h`` / ``2d`` for the age of the index."""
    if seconds is None:
        return "unknown"
    if seconds < 0:
        return "in the future"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds / 60)}m"
    if seconds < 172800:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def overview(
    store: "IndexStore",
    *,
    package_limit: int = 0,
    key_symbol_limit: int = OVERVIEW_KEY_SYMBOLS,
) -> str:
    """The orientation page: what is indexed, where the packages are."""
    config = store.config
    stats = store.stats
    lines = [
        f"project: {config.project_root}",
        f"index:   {config.index_dir}",
    ]
    source = "explicit" if config.explicit_index else "default (.ai-index)"
    lines.append(f"         source: {source}, generated: {stats.generated_at or 'unknown'}"
                 f" ({human_age(store.index_age_seconds())} ago)")
    module = stats.module_path or "(unknown)"
    go_version = f" (go {stats.go_version})" if stats.go_version else ""
    lines.append(f"module:  {module}{go_version}")
    lines.append(
        "totals:  "
        + count_note(stats.packages, "package")
        + ", "
        + count_note(stats.files, "file")
        + ", "
        + count_note(stats.symbols, "symbol")
    )
    lines.append(
        f"calls:   {stats.resolved_calls} resolved to a symbol id, "
        f"{stats.unresolved_calls} left as raw names (unresolved_calls)"
    )
    health = [("stats.json" if not store.stats_missing else "stats.json MISSING (values derived)")]
    health.append(f"{stats.parse_errors} go files with parse errors")
    health.append(
        count_note(len(store.problems), "malformed index line") if store.problems else "index clean"
    )
    lines.append("health:  " + "; ".join(health))

    groups = store.package_groups
    lines.append("")
    lines.append(_packages_header(groups))
    shown = groups if package_limit <= 0 else groups[:package_limit]
    dir_width, name_width = _column_widths(shown)
    for group in shown:
        tags = []
        if group.test_package:
            tags.append("has tests")
        if group.record is None:
            tags.append("no package clause")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"{INDENT}{group.directory or '(root)':<{dir_width}}  "
            f"{group.name:<{name_width}}  "
            f"{len(group.files):>3} files "
            f"{len(group.symbols):>5} symbols  "
            f"{group.package_path}{suffix}"
        )
    note = truncation_note(len(shown), len(groups))
    if note:
        lines.append(note)

    key = [s for s in store.symbols if s.exported and s.kind in IMPORTANT_KINDS]
    key.sort(key=Symbol.sort_key)
    shown_key = key if key_symbol_limit <= 0 else key[:key_symbol_limit]
    lines.append("")
    lines.append(f"entry points ({len(shown_key)} of {len(key)} exported declarations):")
    for symbol in shown_key:
        lines.append(symbol_line(symbol))
    note = truncation_note(len(shown_key), len(key))
    if note:
        lines.append(note)
    return "\n".join(lines)


def _packages_header(groups: Sequence["PackageGroup"]) -> str:
    """``packages (7):`` -- plus the groups a package count would not include.

    ``stats.json`` counts package records; a file without a package clause is
    still indexed, and still gets its own group here, so the two numbers can
    differ by exactly those files.
    """
    header = f"packages ({len(groups)}"
    orphaned = sum(1 for group in groups if group.record is None)
    if orphaned:
        header += f", {orphaned} without a package clause"
    return header + "):"


def packages(store: "IndexStore") -> str:
    """Every package, folded before files, at a glance."""
    groups = store.package_groups
    lines = [_packages_header(groups)]
    dir_width, name_width = _column_widths(groups)
    for group in groups:
        tags = []
        if group.test_package:
            tags.append("tests")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"{INDENT}{group.directory or '(root)':<{dir_width}}  "
            f"{group.name:<{name_width}}  "
            f"{len(group.files):>3} files "
            f"{len(group.symbols):>5} symbols  "
            f"{group.package_path}{suffix}"
        )
    lines.append("")
    lines.append(
        f"{INDENT}page per package: "
        + ", ".join(
            str(store.config.package_page(page_slug(g.directory, g.name)).relative_to(store.config.index_dir))
            for g in groups[:3]
        )
        + (", ..." if len(groups) > 3 else "")
    )
    return "\n".join(lines)


def _column_widths(
    groups: Sequence["PackageGroup"],
) -> tuple[int, int]:
    """Directory and package-name column widths for a package table."""
    directories = [group.directory or "(root)" for group in groups] or ["(root)"]
    names = [group.name or "-" for group in groups] or ["-"]
    return max(len(name) for name in directories), max(len(name) for name in names)


def page_slug(directory: str, package_name: str) -> str:
    """Page stem used by the indexer for a package (mirrors ``aindex.render``)."""
    stem = directory.replace("/", "_").replace("\\", "_") or "root"
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in stem)
    if package_name.endswith("_test"):
        cleaned += "-test"
    return cleaned.strip("-.") or "root"


def find_package_page(packages_dir: Path, directory: str, package_name: str) -> Path | None:
    """The generated ``packages/<slug>.md`` for a package, if it exists."""
    if not packages_dir.is_dir():
        return None
    slug = page_slug(directory, package_name)
    exact = packages_dir / f"{slug}.md"
    if exact.is_file():
        return exact
    for candidate in sorted(packages_dir.glob(f"{slug}-[0-9]*.md")):
        return candidate
    return None


def indent_text(text: str, prefix: str = INDENT) -> str:
    """Indent every line of ``text`` (used to quote docs verbatim)."""
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.splitlines())


def truncation_note(shown: int, total: int) -> str:
    """``showing 10 of 53 (use --limit 0 for all)`` when the list was cut."""
    if total <= shown:
        return ""
    return f"{INDENT}showing {shown} of {total} (use --limit 0 for all)"


def count_note(count: int, singular: str, plural: str = "") -> str:
    """``1 package`` / ``5 packages``."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


def symbol_line(symbol: Symbol) -> str:
    """One-line card: ``kind qualified_name  file:lines  - doc``."""
    parts = [f"{_KIND_LABELS.get(symbol.kind, symbol.kind)} {symbol.qualified_name}"]
    if symbol.location:
        parts.append(symbol.location)
    line = INDENT + "  ".join(parts)
    if symbol.doc_line:
        line += f"  - {symbol.doc_line}"
    return line


def relation_lines(
    label: str,
    symbols: Sequence[Symbol],
    missing: Sequence[str] = (),
    *,
    limit: int = 0,
) -> list[str]:
    """Render a relationship list: resolved symbols by id, then unknown names."""
    total = len(symbols) + len(missing)
    if total == 0:
        return [f"{INDENT}{label}: (none in the index)"]
    lines = [f"{INDENT}{label} ({total}):"]
    shown = symbols if limit <= 0 else list(symbols)[:limit]
    for symbol in shown:
        lines.append(f"{INDENT}{INDENT}{symbol.id}")
    if total > len(shown):
        lines.append(f"{INDENT}{INDENT}... and {total - len(shown)} more")
    for name in missing:
        lines.append(f"{INDENT}{INDENT}{name}  [unresolved: not a known id]")
    return lines


RAW_CALL_NOTE = "[raw name: stdlib, another package or a field]"


def unresolved_lines(names: Sequence[str], *, label: str = "unresolved calls") -> list[str]:
    """Call names the linker kept verbatim instead of guessing at an id.

    These are not dangling ids: they are spellings (``fmt.Println``, ``t.Fatalf``)
    that never had a candidate inside the indexed packages.
    """
    if not names:
        return []
    lines = [f"{INDENT}{label} ({len(names)}):"]
    for name in names:
        lines.append(f"{INDENT}{INDENT}{name}  {RAW_CALL_NOTE}")
    return lines


def doc_block(symbol: Symbol) -> list[str]:
    """The symbol's documentation, quoted verbatim and indented."""
    if not symbol.doc:
        return []
    return [f"{INDENT}doc:", indent_text(symbol.doc, INDENT + INDENT)]


def field_list(label: str, values: Sequence[str], *, limit: int = 0) -> list[str]:
    """A labelled list of raw strings (fields, methods, calls, ...)."""
    if not values:
        return []
    shown = values if limit <= 0 else list(values)[:limit]
    lines = [f"{INDENT}{label} ({len(values)}):"]
    for value in shown:
        lines.append(f"{INDENT}{INDENT}{value}")
    if len(values) > len(shown):
        lines.append(f"{INDENT}{INDENT}... and {len(values) - len(shown)} more")
    return lines


def preview(text: str, width: int = DOC_PREVIEW_CHARS) -> str:
    """Fold ``text`` onto one line and cut it at ``width``."""
    folded = " ".join(text.split())
    if width > 0 and len(folded) > width:
        return folded[: width - 3].rstrip() + "..."
    return folded


def display_path(path: Path, config: object) -> str:
    """``path`` relative to the project root when it lives under it."""
    root = Path(getattr(config, "project_root"))
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def package_detail(
    store: "IndexStore",
    group: "PackageGroup",
    *,
    symbol_limit: int = PACKAGE_SYMBOL_LIMIT,
) -> str:
    """Everything the index knows about one package: files, then symbols."""
    lines = [
        f"package {group.name}  ({group.package_path})",
        f"{INDENT}directory:  {group.directory or '(root)'}",
        f"{INDENT}files:      {len(group.files)}",
        f"{INDENT}symbols:    {len(group.symbols)}"
        + (" (including tests)" if group.test_package else ""),
    ]
    page = find_package_page(store.config.packages_dir, group.directory, group.name)
    if page is not None:
        lines.append(f"{INDENT}index page: {display_path(page, store.config)}")
    if group.record is not None and group.record.doc:
        lines.append(f"{INDENT}package doc:")
        lines.extend(indent_text(group.record.doc, INDENT + INDENT).splitlines())

    lines.append("")
    lines.append(f"files ({len(group.files)}):")
    file_width = max((len(record.file) for record in group.files), default=10)
    for record in group.files:
        flags = []
        if record.test_related:
            flags.append("test")
        if record.parse_error:
            flags.append("PARSE ERROR")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"{INDENT}{record.file:<{file_width}}  "
            f"{count_note(record.symbols, 'symbol'):>11}  "
            f"{count_note(record.lines, 'line'):>10}{suffix}"
        )

    unlimited = symbol_limit <= 0
    remaining = symbol_limit
    for kind in KIND_ORDER:
        members = group.symbols_of((kind,))
        if not members:
            continue
        lines.append("")
        lines.append(f"{kind}s ({len(members)}):")
        picked = members if unlimited else members[: max(remaining, 0)]
        for symbol in picked:
            lines.append(symbol_line(symbol))
            if symbol.signature:
                lines.append(f"{INDENT}{INDENT}{preview(symbol.signature, 180)}")
            extras = []
            if symbol.fields:
                extras.append(count_note(len(symbol.fields), "field"))
            if symbol.methods:
                extras.append(count_note(len(symbol.methods), "method"))
            if symbol.types:
                extras.append("types: " + ", ".join(symbol.types[:6]))
            if extras:
                lines.append(f"{INDENT}{INDENT}{INDENT}({'; '.join(extras)})")
        if len(members) > len(picked):
            lines.append(f"{INDENT}{INDENT}... and {len(members) - len(picked)} more")
        if not unlimited:
            remaining = max(remaining - len(picked), 0)
    return "\n".join(lines)


def symbol_flags(symbol: Symbol) -> str:
    """``func  exported  test`` -- the one-line trait summary used in lists."""
    traits = [_KIND_LABELS.get(symbol.kind, symbol.kind)]
    if symbol.exported:
        traits.append("exported")
    if symbol.test_related:
        traits.append("test")
    if symbol.receiver_type:
        traits.append(f"receiver {symbol.receiver_type}")
    return "  ".join(traits)


def search_outcome(store: "IndexStore", outcome: "SearchOutcome") -> str:
    """A ranked list: score, id, traits, location, signature, doc."""
    results = outcome.results
    if not results:
        return "\n".join(
            [
                f"query: {outcome.query}",
                f"{INDENT}no symbol matched",
                f"{INDENT}hint: try a shorter query or a single word; "
                "`aindex-query overview` shows the package map",
            ]
        )
    cap = "" if not outcome.truncated else "; raise --limit for more"
    lines = [
        f"query: {outcome.query}",
        f"{INDENT}{outcome.total} match{'es' if outcome.total != 1 else ''}, "
        f"showing {len(results)}{cap}",
    ]
    for rank, result in enumerate(results, start=1):
        symbol = result.symbol
        flags = symbol_flags(symbol)
        lines.append("")
        lines.append(f"{INDENT}{rank:>3}. {result.score:.4f}  {symbol.id}")
        lines.append(
            f"{INDENT}{INDENT}{flags}{' ' * max(1, FLAG_COLUMN - len(flags))}"
            f"{symbol.location}"
        )
        if symbol.signature:
            lines.append(f"{INDENT}{INDENT}{preview(symbol.signature, 160)}")
        if symbol.doc_line:
            lines.append(f"{INDENT}{INDENT}{symbol.doc_line}")
    return "\n".join(lines)


def symbol_detail(store: "IndexStore", symbol: Symbol) -> str:
    """One record in full: identity, signature, doc and both directions."""
    lines = [
        f"symbol: {symbol.id}",
        f"{INDENT}kind:       {_KIND_LABELS.get(symbol.kind, symbol.kind)}",
        f"{INDENT}package:    {symbol.package}  ({symbol.package_path})",
        f"{INDENT}file:       {symbol.location or symbol.file}",
        f"{INDENT}exported:   {'yes' if symbol.exported else 'no'}"
        f"{'   test_related: yes' if symbol.test_related else ''}",
    ]
    if symbol.receiver:
        lines.append(f"{INDENT}receiver:   {symbol.receiver}  ({symbol.receiver_type})")
    if symbol.signature:
        lines.append(f"{INDENT}signature:")
        lines.append(indent_text(symbol.signature, INDENT + INDENT))
    lines.extend(doc_block(symbol))
    lines.extend(field_list("fields", symbol.fields))
    lines.extend(field_list("methods", symbol.methods))
    lines.extend(field_list("types", symbol.types))
    lines.extend(field_list("imports", symbol.imports))

    resolved, dangling = store.callees_of(symbol)
    lines.append("")
    lines.extend(relation_lines("calls (resolved to ids)", resolved, dangling))
    lines.extend(unresolved_lines(symbol.unresolved_calls))
    lines.extend(relation_lines("callers (from the whole index)", store.callers_of(symbol.id)))
    return "\n".join(lines)


def edges(
    title: str,
    query: str,
    symbols: Sequence[Symbol],
    missing: Sequence[str] = (),
    *,
    raw: Sequence[str] = (),
    limit: int = 0,
) -> str:
    """``callers of <id>`` / ``callees of <id>`` -- ids, plus raw names."""
    total = len(symbols) + len(missing)
    lines = [f"{title}: {query}", f"{INDENT}{total} found"]
    if total == 0:
        lines.append(f"{INDENT}(nothing in the index points this way)")
    else:
        lines.extend(relation_lines("symbols", symbols, missing, limit=limit))
    lines.extend(unresolved_lines(raw))
    return "\n".join(lines)


def read_range(
    label: str,
    text_lines: Sequence[str],
    start: int,
    end: int,
    total: int,
    *,
    truncated: bool = False,
) -> str:
    """Numbered source lines, exactly as they are on disk."""
    if not text_lines:
        if total == 0:
            detail = "the file is empty"
        elif start > total:
            detail = f"the file has {total} lines; the range starts past its end"
        else:
            detail = f"nothing in the requested range ({start}-{end})"
        return "\n".join(
            [f"file: {label}  ({count_note(total, 'line')})", f"{INDENT}{detail}"]
        )
    header = f"file: {label}  (lines {start}-{end} of {total})"
    width = len(str(max(end, 1)))
    body = [
        f"{number:>{width}}\t{text}"
        for number, text in zip(range(start, start + len(text_lines)), text_lines)
    ]
    if truncated:
        body.append(
            f"{INDENT}... truncated after {len(text_lines)} lines "
            "(narrow the range or raise --limit)"
        )
    return "\n".join([header, *body])


def doctor_report(
    index_dir: str,
    checks: Sequence[tuple[str, str, str, Sequence[str]]],
    *,
    examples: int = 0,
) -> str:
    """A check list; each line is ``ok``, ``warn`` or ``fail`` plus a detail."""
    lines = [f"index: {index_dir}", "", f"checks ({len(checks)}):"]
    for name, status, detail, evidence in checks:
        lines.append(f"{INDENT}{status:<5} {name:<26} {detail}")
        shown = evidence if examples <= 0 else list(evidence)[:examples]
        for item in shown:
            lines.append(f"{INDENT}{' ' * 6}{item}")
        if len(evidence) > len(shown):
            lines.append(f"{INDENT}{' ' * 6}... and {len(evidence) - len(shown)} more")
    failures = sum(1 for _, status, _, _ in checks if status == "fail")
    warnings = sum(1 for _, status, _, _ in checks if status == "warn")
    lines.append("")
    verdict = (
        "the index is usable"
        if not failures
        else "the index is incomplete - regenerate it before trusting the answers"
    )
    lines.append(f"{INDENT}{failures} failing, {warnings} warning(s): {verdict}")
    return "\n".join(lines)


def resolve_failure(query: str, candidates: Sequence[Symbol], *, limit: int = 0) -> str:
    """What ``symbol``/``callers`` print when a reference is not a single id."""
    if not candidates:
        return "\n".join(
            [
                f"not found: {query}",
                f"{INDENT}no symbol matches this reference; ids look like",
                f"{INDENT}{INDENT}func:example.com/demo/internal/store:New",
                f"{INDENT}{INDENT}method:example.com/demo/internal/store:memory.Get",
                f"{INDENT}try `aindex-query search {query}` to rank near-misses",
            ]
        )
    lines = [
        f"ambiguous: {query}",
        f"{INDENT}{len(candidates)} symbols match; pass one of these ids exactly:",
    ]
    lines.extend(relation_lines("candidates", candidates, limit=limit))
    return "\n".join(lines)

