"""Typed exceptions and the process exit codes they map to.

Every expected failure of the CLI is one of the classes below, and every class
carries the exit code an agent can branch on without parsing prose:

===  ==================================================
0    success
1    invalid usage
2    index not found
3    symbol not found
4    invalid or corrupted index
5    source file / read error
6    security error (path traversal, symlink escape)
===  ==================================================

The numbers are part of the public contract (see the README), so they are
defined once, here, and never repeated as literals in the commands.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INDEX_NOT_FOUND = 2
EXIT_SYMBOL_NOT_FOUND = 3
EXIT_INVALID_INDEX = 4
EXIT_SOURCE_READ_ERROR = 5
EXIT_SECURITY = 6


class QueryError(Exception):
    """Base class for failures the CLI reports instead of a traceback.

    ``hint`` is a short, actionable second line (usually "re-run the indexer"),
    printed under the message so an agent knows how to recover.
    """

    exit_code = EXIT_USAGE

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def format(self) -> str:
        """Render the error for stderr: message first, then the hint."""
        text = f"error: {self.message}"
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text


class UsageError(QueryError):
    """The command line itself is wrong (bad range, empty query, ...)."""

    exit_code = EXIT_USAGE


class IndexNotFoundError(QueryError):
    """No ``.ai-index`` was found where one was expected."""

    exit_code = EXIT_INDEX_NOT_FOUND

    def __init__(self, index_dir: str, message: str = "", *, hint: str = "") -> None:
        self.index_dir = index_dir
        super().__init__(
            message or f"no index found at {index_dir}",
            hint=hint
            or "build the index first: python3 aindex-make <project root>",
        )


class SymbolNotFoundError(QueryError):
    """A symbol, package or caller id is not present in the index."""

    exit_code = EXIT_SYMBOL_NOT_FOUND


class InvalidIndexError(QueryError):
    """The index exists but is unreadable or structurally broken."""

    exit_code = EXIT_INVALID_INDEX

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(
            message,
            hint=hint
            or "the index looks stale or corrupted; regenerate it with "
            "python3 aindex-make <project root>",
        )


class SourceReadError(QueryError):
    """A requested source file could not be read."""

    exit_code = EXIT_SOURCE_READ_ERROR


class SecurityError(QueryError):
    """A path escaped the project root (traversal, absolute path, symlink)."""

    exit_code = EXIT_SECURITY


def exit_code_for(error: BaseException) -> int:
    """The exit status to use for ``error`` (fallback: invalid index)."""
    if isinstance(error, QueryError):
        return error.exit_code
    return EXIT_INVALID_INDEX
