"""Command line interface: read-only queries against a generated ``.ai-index/``.

    aindex-query overview
    aindex-query packages
    aindex-query package internal/store
    aindex-query search "New store" --kind func --limit 5
    aindex-query symbol 'method:example.com/demo/internal/store:memory.Get'
    aindex-query callers memory.Get
    aindex-query callees memory.Get
    aindex-query read memory.Get --context 2
    aindex-query doctor

Every command reads the index (and, only for ``read``, one source file).  Text
goes to stdout, and ``--json`` replaces that text with a single JSON document --
the two are never mixed, so an agent can pipe a command straight into a parser.
Failures go to stderr with the exit code documented in
:mod:`aindex_query.errors`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, NoReturn, Sequence

from . import __version__, render
from .config import Config, resolve_config
from .constants import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_EDGES_LIMIT,
    DEFAULT_READ_LINES,
    DEFAULT_SEARCH_LIMIT,
    IMPORTANT_KINDS,
    KIND_ORDER,
    MAX_DOCTOR_EXAMPLES,
    OVERVIEW_KEY_SYMBOLS,
    PACKAGE_SYMBOL_LIMIT,
    REQUIRED_INDEX_FILES,
    SYMBOLS_FILENAME,
)
from .errors import (
    EXIT_INVALID_INDEX,
    EXIT_OK,
    EXIT_SYMBOL_NOT_FOUND,
    EXIT_USAGE,
    QueryError,
    SymbolNotFoundError,
    UsageError,
)
from .index_store import IndexStore, PackageGroup
from .models import CandidateList, Symbol
from .search import SearchFilters, search
from .source_reader import SourceSlice, looks_like_path, read_slice, splitting_range

#: Every sub-command, mapped to its handler (filled in at the end of the module).
_COMMANDS: dict[str, Callable[[IndexStore, argparse.Namespace], int]] = {}


def _add_common(parser: argparse.ArgumentParser) -> None:
    """``--root`` / ``--index`` / ``--json`` -- also accepted after a command.

    ``SUPPRESS`` as the default is what makes that work: the sub-parser only
    sets the attribute when the flag was actually given, so ``aindex-query
    search Foo --json`` behaves exactly like ``aindex-query --json search Foo``.
    """
    parser.add_argument(
        "--root",
        default=argparse.SUPPRESS,
        metavar="DIR",
        help="project root (default: current directory)",
    )
    parser.add_argument(
        "--index",
        default=argparse.SUPPRESS,
        metavar="DIR",
        help="index directory (default: $AINDEX_INDEX_DIR or <root>/.ai-index)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print one JSON document instead of text",
    )



class _Parser(argparse.ArgumentParser):
    """An ``argparse`` parser that fails with *our* usage exit code.

    ``argparse`` calls ``sys.exit(2)`` on a usage error, which would collide with
    the documented "index not found" code, so the CLI re-reports the error itself.
    """

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        print(
            f"{render.INDENT}hint: run `aindex-query --help` for the command list",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    """The CLI definition (also used by the tests)."""
    parser = _Parser(
        prog="aindex-query",
        description=(
            "Read-only queries against a generated .ai-index/ of a Go project: "
            "navigate packages, symbols, the call graph and source ranges without "
            "reading the whole tree.  Python 3.11+, standard library only."
        ),
        epilog=(
            "exit codes: 0 ok, 1 usage, 2 index missing, 3 symbol not found or "
            "ambiguous, 4 broken index, 5 unreadable source, 6 path escapes the root"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--root",
        default=".",
        metavar="DIR",
        help="project root (default: current directory)",
    )
    parser.add_argument(
        "--index",
        default=None,
        metavar="DIR",
        help="index directory (default: $AINDEX_INDEX_DIR or <root>/.ai-index)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON document instead of text",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    overview = sub.add_parser(
        "overview", help="what is indexed: totals, health, the package map"
    )
    _add_common(overview)
    overview.add_argument(
        "--packages",
        type=int,
        default=0,
        metavar="N",
        help="show at most N packages (default: all)",
    )
    overview.add_argument(
        "--limit",
        type=int,
        default=OVERVIEW_KEY_SYMBOLS,
        metavar="N",
        help="show at most N entry points (default: %(default)s, 0 for all)",
    )

    packages = sub.add_parser("packages", help="list every package")
    _add_common(packages)

    package = sub.add_parser(
        "package", help="one package in detail: files, symbols, signatures"
    )
    _add_common(package)
    package.add_argument("name", help="package name, import path or directory")
    package.add_argument(
        "--limit",
        type=int,
        default=PACKAGE_SYMBOL_LIMIT,
        metavar="N",
        help="show at most N symbols (default: %(default)s, 0 for all)",
    )

    find = sub.add_parser("search", help="rank symbols by how the query matched")
    _add_common(find)
    find.add_argument("query", help="words to look for")
    find.add_argument(
        "--kind",
        action="append",
        default=[],
        choices=list(KIND_ORDER),
        metavar="KIND",
        help="only this kind (repeatable); one of: " + ", ".join(KIND_ORDER),
    )
    find.add_argument(
        "--package", default="", metavar="PATH", help="only this package import path"
    )
    find.add_argument("--file", default="", metavar="PATH", help="only this file")
    find.add_argument(
        "--exported", action="store_true", help="only exported symbols"
    )
    find.add_argument(
        "--no-tests", action="store_true", help="skip test files and Test functions"
    )

    find.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        metavar="N",
        help="show at most N results (default: %(default)s, 0 for all)",
    )

    symbol = sub.add_parser(
        "symbol", help="one symbol in full: signature, doc, calls and callers"
    )
    _add_common(symbol)
    symbol.add_argument(
        "reference",
        help="id, Type.Method, import.path.Name, directory.Name or a bare name",
    )
    symbol.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_CANDIDATE_LIMIT,
        metavar="N",
        help="list at most N candidates when the reference is ambiguous "
        "(default: %(default)s)",
    )

    callers = sub.add_parser("callers", help="which symbols call this one")
    _add_common(callers)
    callers.add_argument("reference", help="the callee, in any accepted form")
    callers.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_EDGES_LIMIT,
        metavar="N",
        help="list at most N ids (default: %(default)s, 0 for all)",
    )

    callees = sub.add_parser(
        "callees", help="which symbols this one calls (plus raw names)"
    )
    _add_common(callees)
    callees.add_argument("reference", help="the caller, in any accepted form")
    callees.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_EDGES_LIMIT,
        metavar="N",
        help="list at most N ids (default: %(default)s, 0 for all)",
    )

    reader = sub.add_parser(
        "read", help="print a source range (from a symbol or a relative path)"
    )
    _add_common(reader)
    reader.add_argument(
        "target",
        help="a symbol reference, or a location relative to the project root: "
        "file.go, file.go:12 or file.go:12-34",
    )
    reader.add_argument("--start", type=int, default=None, metavar="N",
                        help="first line (1-based; default: the symbol's first line)")
    reader.add_argument("--end", type=int, default=None, metavar="N",
                        help="last line (default: the symbol's last line)")
    reader.add_argument(
        "--context",
        type=int,
        default=0,
        metavar="N",
        help="also print N lines before and after the range (default: 0)",
    )
    reader.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_READ_LINES,
        metavar="N",
        help="at most N lines (default: %(default)s, 0 for no limit)",
    )
    reader.add_argument(
        "--symbol",
        action="store_true",
        help="treat TARGET as a symbol reference even if it looks like a path",
    )

    doctor = sub.add_parser(
        "doctor", help="check that the index is complete, consistent and current"
    )
    _add_common(doctor)
    doctor.add_argument(
        "--limit",
        type=int,
        default=MAX_DOCTOR_EXAMPLES,
        metavar="N",
        help="show at most N examples per failing check (default: %(default)s, 0 for all)",
    )
    return parser


def _non_negative(value: int, name: str) -> int:
    """Reject a negative limit early, with the exit code of a usage error."""
    if value < 0:
        raise UsageError(f"{name} cannot be negative (use 0 for no limit)")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; returns the process exit code (never raises)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    try:
        exit_code = _dispatch(args)
        # Flush here, so a broken pipe surfaces inside this try block instead of
        # during the interpreter's own shutdown flush (which cannot be handled).
        sys.stdout.flush()
    except QueryError as error:
        print(error.format(), file=sys.stderr)
        return error.exit_code
    except BrokenPipeError:
        # The reader went away (`| head`): stop printing, but stay quiet about it
        # -- and make sure the interpreter's final flush of stdout does not
        # resurrect the same error on the way out.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:  # pragma: no cover - stdout already unusable
            pass
        return EXIT_OK
    return exit_code


def _dispatch(args: argparse.Namespace) -> int:
    """Resolve the config, then hand over to the command."""
    config = resolve_config(root=args.root, index=args.index)
    if args.command == "doctor":
        return _cmd_doctor(config, args)
    config.require_index()
    store = IndexStore(config)
    handler = _COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse rejects unknown commands
        raise UsageError(f"unknown command: {args.command}")
    return handler(store, args)


def _emit(args: argparse.Namespace, text: str, payload: dict[str, Any]) -> None:
    """Print the text, or the JSON document -- never both."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text)


def _resolve(store: IndexStore, reference: str, limit: int) -> CandidateList:
    """Resolve a reference, keeping the candidate cap meaningful."""
    return store.resolve(reference, limit=limit or DEFAULT_CANDIDATE_LIMIT)


def _unresolved(args: argparse.Namespace, resolved: CandidateList) -> int:
    """Report a missing or ambiguous reference; exit code 3 either way."""
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "query": resolved.query,
                    "found": False,
                    "ambiguous": resolved.ambiguous,
                    "truncated": resolved.truncated,
                    "candidates": [symbol.card() for symbol in resolved.candidates],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(
            render.resolve_failure(
                resolved.query,
                resolved.candidates,
                limit=DEFAULT_CANDIDATE_LIMIT,
            ),
            file=sys.stderr,
        )
    return EXIT_SYMBOL_NOT_FOUND


def _cmd_overview(store: IndexStore, args: argparse.Namespace) -> int:
    package_limit = _non_negative(args.packages, "--packages")
    key_limit = _non_negative(args.limit, "--limit")
    text = render.overview(store, package_limit=package_limit, key_symbol_limit=key_limit)
    groups = store.package_groups
    capped = groups if package_limit == 0 else groups[:package_limit]
    key = [s for s in store.symbols if s.exported and s.kind in IMPORTANT_KINDS]
    key.sort(key=Symbol.sort_key)
    capped_key = key if key_limit == 0 else key[:key_limit]
    payload = {
        "project_root": str(store.config.project_root),
        "index_dir": str(store.config.index_dir),
        "explicit_index": store.config.explicit_index,
        "generated_at": store.stats.generated_at,
        "index_age_seconds": store.index_age_seconds(),
        "module_path": store.stats.module_path,
        "go_version": store.stats.go_version,
        "stats": store.stats.to_dict(),
        "stats_missing": store.stats_missing,
        "problems": [problem.to_dict() for problem in store.problems],
        "packages": [group.to_dict() for group in capped],
        "entry_points": [symbol.card() for symbol in capped_key],
    }
    _emit(args, text, payload)
    return EXIT_OK


def _cmd_packages(store: IndexStore, args: argparse.Namespace) -> int:
    groups = store.package_groups
    payload = {
        "count": len(groups),
        "packages": [group.to_dict() for group in groups],
        "pages": {
            group.key: render.page_slug(group.directory, group.name) for group in groups
        },
    }
    _emit(args, render.packages(store), payload)
    return EXIT_OK


def _cmd_package(store: IndexStore, args: argparse.Namespace) -> int:
    limit = _non_negative(args.limit, "--limit")
    matches = store.find_package(args.name)
    if not matches:
        print(
            f"error: no package matches {args.name!r}\n"
            f"  hint: `aindex-query packages` lists all {len(store.package_groups)} packages",
            file=sys.stderr,
        )
        return EXIT_SYMBOL_NOT_FOUND
    if len(matches) > 1:
        candidates = [symbol_source(group) for group in matches]
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "query": args.name,
                        "found": False,
                        "ambiguous": True,
                        "candidates": candidates,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"ambiguous package: {args.name}", file=sys.stderr)
            for group in matches:
                print(
                    f"  {group.name}  {group.package_path}  "
                    f"({len(group.symbols)} symbols, {len(group.files)} files)",
                    file=sys.stderr,
                )
        return EXIT_SYMBOL_NOT_FOUND
    group = matches[0]
    page = render.find_package_page(
        store.config.packages_dir, group.directory, group.name
    )
    payload = {
        "package": group.to_dict(),
        "package_page": str(page) if page is not None else None,
        "files": [record.to_dict() for record in group.files],
        "symbols": [symbol.card() for symbol in group.symbols],
        "package_doc": group.record.doc if group.record is not None else "",
    }
    _emit(args, render.package_detail(store, group, symbol_limit=limit), payload)
    return EXIT_OK


def symbol_source(group: PackageGroup) -> dict[str, Any]:
    """A package as a JSON candidate (no symbols, just identity)."""
    return {
        "name": group.name,
        "package_path": group.package_path,
        "directory": group.directory,
        "files": len(group.files),
        "symbols": len(group.symbols),
    }


def _search_package_path(store: IndexStore, text: str) -> str:
    """Turn ``--package`` into an exact import path (names are accepted)."""
    wanted = text.strip()
    if not wanted:
        return ""
    known = {group.package_path for group in store.package_groups}
    if wanted in known:
        return wanted
    matches = store.find_package(wanted)
    if len(matches) == 1:
        return matches[0].package_path
    if not matches:
        raise UsageError(
            f"--package {text!r} matches no indexed package",
            hint="run `aindex-query packages` for the list of import paths",
        )
    options = ", ".join(sorted(group.package_path for group in matches))
    raise UsageError(
        f"--package {text!r} is ambiguous: {options}",
        hint="pass the full import path",
    )


def _search_file(store: IndexStore, text: str) -> str:
    """Turn ``--file`` into the exact path stored in ``files.jsonl``."""
    wanted = text.strip().lstrip("./")
    if not wanted:
        return ""
    if wanted in store.files_by_path:
        return wanted
    suffix = sorted(
        path for path in store.files_by_path if path == wanted or path.endswith("/" + wanted)
    )
    if len(suffix) == 1:
        return suffix[0]
    if not suffix:
        raise UsageError(
            f"--file {text!r} is not in the index",
            hint="paths look like internal/store/store.go (relative to the project root)",
        )
    raise UsageError(
        f"--file {text!r} is ambiguous: {', '.join(suffix)}",
        hint="pass the full path from the project root",
    )


def _cmd_search(store: IndexStore, args: argparse.Namespace) -> int:
    limit = _non_negative(args.limit, "--limit")
    filters = SearchFilters(
        kinds=tuple(args.kind),
        package_path=_search_package_path(store, args.package),
        file=_search_file(store, args.file),
        exported_only=args.exported,
        include_tests=not args.no_tests,
    )
    outcome = search(store.symbols, args.query, filters=filters, limit=limit)
    payload = {
        "query": outcome.query,
        "total": outcome.total,
        "limit": outcome.limit,
        "filters": {
            "kinds": list(filters.kinds),
            "package_path": filters.package_path,
            "file": filters.file,
            "exported_only": filters.exported_only,
            "include_tests": filters.include_tests,
        },
        "results": [result.to_dict() for result in outcome.results],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render.search_outcome(store, outcome))
        filters_active = bool(
            filters.kinds
            or filters.package_path
            or filters.file
            or filters.exported_only
            or not filters.include_tests
        )
        if not outcome.results and filters_active:
            print(
                f"{render.INDENT}hint: filters were active "
                "(--kind/--package/--file/--exported/--no-tests)",
                file=sys.stderr,
            )
    return EXIT_OK if outcome.results else EXIT_SYMBOL_NOT_FOUND


def _cmd_symbol(store: IndexStore, args: argparse.Namespace) -> int:
    limit = _non_negative(args.limit, "--limit")
    resolved = _resolve(store, args.reference, limit)
    if not resolved.found:
        return _unresolved(args, resolved)
    symbol = resolved.symbol
    assert symbol is not None  # for type checkers; found == symbol is not None
    callees, dangling = store.callees_of(symbol)
    callers = store.callers_of(symbol.id)
    payload = {
        "symbol": symbol.to_dict(),
        "calls": [target.card() for target in callees],
        "dangling_calls": list(dangling),
        "callers": [caller.card() for caller in callers],
    }
    _emit(args, render.symbol_detail(store, symbol), payload)
    return EXIT_OK


def _cmd_callers(store: IndexStore, args: argparse.Namespace) -> int:
    limit = _non_negative(args.limit, "--limit")
    resolved = _resolve(store, args.reference, DEFAULT_CANDIDATE_LIMIT)
    if not resolved.found:
        return _unresolved(args, resolved)
    symbol = resolved.symbol
    assert symbol is not None
    callers = store.callers_of(symbol.id)
    payload = {
        "query": args.reference,
        "symbol": symbol.id,
        "count": len(callers),
        "callers": [caller.card() for caller in callers],
    }
    text = render.edges("callers of", symbol.id, callers, limit=limit)
    if not callers:
        text += (
            f"\n{render.INDENT}the index resolves calls inside the indexed packages; "
            "unexported entry points and reflection stay invisible"
        )
    _emit(args, text, payload)
    return EXIT_OK


def _cmd_callees(store: IndexStore, args: argparse.Namespace) -> int:
    limit = _non_negative(args.limit, "--limit")
    resolved = _resolve(store, args.reference, DEFAULT_CANDIDATE_LIMIT)
    if not resolved.found:
        return _unresolved(args, resolved)
    symbol = resolved.symbol
    assert symbol is not None
    callees, dangling = store.callees_of(symbol)
    raw = list(symbol.unresolved_calls)
    payload = {
        "query": args.reference,
        "symbol": symbol.id,
        "count": len(callees),
        "dangling_count": len(dangling),
        "unresolved_count": len(raw),
        "callees": [target.card() for target in callees],
        "dangling_calls": list(dangling),
        "unresolved_calls": raw,
    }
    _emit(
        args,
        render.edges("callees of", symbol.id, callees, dangling, raw=raw, limit=limit),
        payload,
    )
    return EXIT_OK


def _cmd_read(store: IndexStore, args: argparse.Namespace) -> int:
    """Print a source range: from a symbol's own range, or from a path."""
    max_lines = _non_negative(args.limit, "--limit")
    context = _non_negative(args.context, "--context")
    for name, value in (("--start", args.start), ("--end", args.end)):
        if value is not None and value < 1:
            raise UsageError(f"{name} is 1-based and must be >= 1")

    symbol: Symbol | None = None
    start, end = args.start, args.end
    target = args.target
    if start is None and end is None:
        # ``read internal/store/store.go:44-46``: the location spelling the index
        # itself prints.  Symbol ids never end in ``:N``/``:N-M``, so this is safe.
        target, suffix_start, suffix_end = splitting_range(target)
        start, end = suffix_start, suffix_end
    elif splitting_range(target)[1] is not None:
        raise UsageError(
            f"pass the line range once: drop either the suffix in {args.target!r} "
            "or --start/--end"
        )
    if args.symbol or not looks_like_path(target):
        resolved = store.resolve(target, limit=DEFAULT_CANDIDATE_LIMIT)
        if not resolved.found:
            return _unresolved(args, resolved)
        symbol = resolved.symbol
        if symbol is None or not symbol.file:
            raise SymbolNotFoundError(
                f"{target!r} has no source file in the index",
                hint="package records carry no source range; pass a .go path instead",
            )
        file_path = symbol.file
        if start is None:
            start = symbol.start_line or None
        if end is None:
            end = symbol.end_line or None
    else:
        file_path = target

    slice_: SourceSlice = read_slice(
        store.config,
        file_path,
        start=start,
        end=end,
        context=context,
        max_lines=max_lines,
    )
    record = store.files_by_path.get(slice_.file)
    parse_error = bool(record and record.parse_error)
    payload: dict[str, Any] = {"symbol": symbol.id if symbol is not None else None,
                               "index_parse_error": parse_error}
    payload.update(slice_.to_dict())
    text = render.read_range(
        slice_.file,
        slice_.lines,
        slice_.start,
        slice_.end,
        slice_.total,
        truncated=slice_.truncated,
    )
    if parse_error:
        text = (
            f"{render.INDENT}note: the indexer recorded a parse error in this file; "
            "the lines below come from the file itself\n" + text
        )
    _emit(args, text, payload)
    return EXIT_OK


def _age_text(seconds: float | None) -> str:
    return f"{render.human_age(seconds)} ago" if seconds is not None else "at an unknown time"


def _duplicate_ids(store: IndexStore) -> tuple[int, list[str]]:
    """Ids that appear twice in ``symbols.jsonl`` (the last record wins in the map)."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for symbol in store.symbols:
        if symbol.id in seen:
            if symbol.id not in duplicates:
                duplicates.append(symbol.id)
        seen.add(symbol.id)
    return len(duplicates), duplicates


def _finish_doctor(
    args: argparse.Namespace,
    config: Config,
    checks: list[tuple[str, str, str, list[str]]],
    problems: list[dict[str, Any]],
    examples: int,
) -> int:
    """Print the check list and map it onto an exit code."""
    failures = sum(1 for _, status, _, _ in checks if status == "fail")
    warnings = sum(1 for _, status, _, _ in checks if status == "warn")
    payload = {
        "index_dir": str(config.index_dir),
        "project_root": str(config.project_root),
        "checks": [
            {"name": name, "status": status, "detail": detail, "evidence": list(evidence)}
            for name, status, detail, evidence in checks
        ],
        "failures": failures,
        "warnings": warnings,
        "problems": problems,
    }
    text = render.doctor_report(str(config.index_dir), checks, examples=examples)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text)
        if failures:
            print(
                f"{render.INDENT}hint: rebuild with "
                f"python3 aindex-make {config.project_root}",
                file=sys.stderr,
            )
    return EXIT_INVALID_INDEX if failures else EXIT_OK


def _cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    """Check the index without ever crashing on a broken one."""
    examples = _non_negative(args.limit, "--limit")
    checks: list[tuple[str, str, str, list[str]]] = []
    if not config.index_dir.is_dir():
        checks.append(
            (
                "index directory",
                "fail",
                f"missing: {config.index_dir}",
                [f"run: python3 aindex-make {config.project_root}"],
            )
        )
        return _finish_doctor(args, config, checks, [], examples)

    checks.append(("index directory", "ok", str(config.index_dir), []))
    missing = [
        name for name in REQUIRED_INDEX_FILES if not (config.index_dir / name).is_file()
    ]
    checks.append(
        (
            "required files",
            "fail" if missing else "ok",
            f"{len(REQUIRED_INDEX_FILES)} expected, {len(missing)} missing",
            [f"{name}: missing" for name in missing],
        )
    )
    if not config.symbols_path.is_file():
        return _finish_doctor(args, config, checks, [], examples)

    store = IndexStore(config)
    return _doctor_checks(args, config, store, checks, examples)


def _doctor_checks(
    args: argparse.Namespace,
    config: Config,
    store: IndexStore,
    checks: list[tuple[str, str, str, list[str]]],
    examples: int,
) -> int:
    """The checks that need a loadable index."""
    problems = [problem.to_dict() for problem in store.problems]
    declarations = sum(1 for symbol in store.symbols if not symbol.is_package)
    checks.append(
        (
            "readable jsonl",
            "fail" if store.problems else "ok",
            f"{len(store.symbols)} symbol records ({declarations} declarations, "
            f"{len(store.symbols) - declarations} package), {len(store.files)} files, "
            f"{len(store.problems)} malformed line(s)",
            [f"{p.path}:{p.line}: {p.message}" for p in store.problems],
        )
    )
    duplicates, duplicate_ids = _duplicate_ids(store)
    checks.append(
        (
            "unique ids",
            "fail" if duplicates else "ok",
            f"{duplicates} duplicated id(s)",
            duplicate_ids,
        )
    )

    stats = store.stats
    if store.stats_missing:
        checks.append(
            (
                "stats.json",
                "warn",
                f"missing or unreadable; totals were derived from {SYMBOLS_FILENAME}",
                [],
            )
        )
    else:
        drift = []
        if stats.symbols != declarations:
            drift.append(f"stats says {stats.symbols} symbols, {declarations} found")
        if stats.files != len(store.files):
            drift.append(f"stats says {stats.files} files, {len(store.files)} found")
        checks.append(
            (
                "stats.json totals",
                "warn" if drift else "ok",
                f"generated {stats.generated_at or 'unknown'}",
                drift,
            )
        )

    dangling, samples = store.dangling_references(limit=MAX_DOCTOR_EXAMPLES)
    checks.append(
        (
            "call edges",
            "warn" if dangling else "ok",
            f"{dangling} call/caller edge(s) point at ids that are not in the index",
            [f"{origin} -> {target} ({field})" for origin, field, target in samples],
        )
    )

    broken = [record.file for record in store.files if record.parse_error]
    checks.append(
        (
            "go parse errors",
            "warn" if broken else "ok",
            f"{len(broken)} file(s) were indexed only partially",
            broken,
        )
    )

    vanished = [
        record.file
        for record in store.files
        if record.file and not (config.project_root / record.file).is_file()
    ]
    checks.append(
        (
            "source files present",
            "fail" if vanished else "ok",
            f"{len(vanished)} indexed file(s) are gone from disk",
            vanished,
        )
    )

    stale = store.stale_files(limit=0)
    if store.stats_missing:
        freshness_note = "no stats.json to date the index from"
    else:
        freshness_note = f"index written {_age_text(store.index_age_seconds())}"
        if stale:
            freshness_note += f"; {len(stale)} source file(s) changed since"
    checks.append(("freshness", "warn" if stale else "ok", freshness_note, stale))
    return _finish_doctor(args, config, checks, problems, examples)


_COMMANDS.update(
    {
        "overview": _cmd_overview,
        "packages": _cmd_packages,
        "package": _cmd_package,
        "search": _cmd_search,
        "symbol": _cmd_symbol,
        "callers": _cmd_callers,
        "callees": _cmd_callees,
        "read": _cmd_read,
    }
)
