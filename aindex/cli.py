"""Command line interface.

    python aindex-make [ROOT] [-o .ai-index] [--clean] [--ignore PATTERN] ...

The command is quiet by default about anything but the numbers: it prints the
location of the index, the run statistics and where to start reading, or the
statistics as JSON with ``--json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .constants import DEFAULT_MAX_DOC_CHARS, DEFAULT_OUTPUT_DIR, PROJECT_FILENAME
from .model import IndexResult
from .pipeline import IndexOptions, run

EXIT_OK = 0
EXIT_ERROR = 1

#: Files listed after a failed or partial parse, before the list is cut short.
MAX_REPORTED_BROKEN_FILES = 5


def build_parser() -> argparse.ArgumentParser:
    """The CLI definition (also used by the tests)."""
    parser = argparse.ArgumentParser(
        prog="aindex-make",
        description=(
            "Build a hierarchical, symbol-level index of a Go project "
            "(.ai-index/) for AI coding agents.  Pure Python, no dependencies."
        ),
        epilog=(
            "The index contains PROJECT.md (overview), packages/*.md (one page "
            "per package), symbols.jsonl, files.jsonl and stats.json."
        ),
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="project root to index (default: current directory)",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help=(
            "output directory; relative values are relative to ROOT "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete the output directory before writing the new index",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="extra .gitignore-style pattern to exclude (repeatable)",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="ignore the project's own .gitignore",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="skip *_test.go files",
    )
    parser.add_argument(
        "--max-doc-chars",
        type=int,
        default=DEFAULT_MAX_DOC_CHARS,
        metavar="N",
        help="truncate stored doc comments to N characters (default: %(default)s)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing but errors",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the statistics as JSON instead of the summary",
    )
    parser.add_argument(
        "--fail-on-parse-error",
        action="store_true",
        help="exit with status 1 when any file could not be parsed",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def options_from_args(args: argparse.Namespace) -> IndexOptions:
    """Turn parsed arguments into :class:`IndexOptions`."""
    return IndexOptions(
        root=args.root,
        output_dir=args.out,
        max_doc_chars=max(args.max_doc_chars, 0),
        extra_ignores=tuple(args.ignore),
        use_gitignore=not args.no_gitignore,
        include_tests=not args.no_tests,
        clean=args.clean,
    )


def report(
    result: IndexResult,
    options: IndexOptions,
    *,
    written: list[str] | None = None,
    as_json: bool = False,
) -> None:
    """Print the run summary (or the JSON form of it)."""
    index_dir = options.resolved_output_dir()
    if as_json:
        payload: dict[str, object] = {
            "output_dir": index_dir,
            "index_files": written or [],
        }
        payload.update(result.stats.to_dict())
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"indexed {result.root} -> {index_dir}")
    for line in result.stats.summary_lines():
        print(f"  {line}")
    print(f"  start here: {os.path.join(index_dir, PROJECT_FILENAME)}")
    broken = [record.file for record in result.files if record.parse_error]
    if broken:
        shown = ", ".join(broken[:MAX_REPORTED_BROKEN_FILES])
        more = " …" if len(broken) > MAX_REPORTED_BROKEN_FILES else ""
        print(f"  unreadable files: {shown}{more}")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    options = options_from_args(args)
    try:
        result, written = run(options)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    if not args.quiet:
        report(result, options, written=written, as_json=args.json)
    if args.fail_on_parse_error and result.stats.parse_errors:
        if not args.quiet:
            print("failing because --fail-on-parse-error was given", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK
