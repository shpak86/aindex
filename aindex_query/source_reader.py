"""Read a slice of a real source file, bounded and guarded.

``read`` is the only command that touches the project's own files.  Three rules
keep that honest:

* the path is always taken from the index -- ``files.jsonl`` records, or a
  symbol's ``file`` -- or typed by the user as *relative* to the project root;
* absolute paths, ``..`` segments and symlinks that point outside the root are
  refused with a :class:`~aindex_query.errors.SecurityError` (exit code 6), the
  check happening on the fully resolved path so ``a/b/../c`` and a symlinked
  directory cannot sneak past it;
* only the requested lines are kept, and never more than
  :data:`~aindex_query.constants.DEFAULT_READ_LINES` at once, so a stray
  ``read`` on a huge generated file cannot flood an agent's context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .constants import DEFAULT_READ_LINES, KIND_ORDER
from .errors import SecurityError, SourceReadError, UsageError

#: Suffixes that make a bare ``read`` argument look like a file, not a symbol.
PATH_MARKERS: tuple[str, ...] = ("/", "\\", ".go")

#: ``func:``, ``method:`` ... -- an id never counts as a path, even though the
#: package path inside it (``func:example.com/demo:main``) contains slashes.
ID_PREFIXES: tuple[str, ...] = tuple(f"{kind}:" for kind in KIND_ORDER)


@dataclass(slots=True)
class SourceSlice:
    """A bounded window of one source file."""

    file: str
    path: Path
    start: int
    end: int
    total: int
    lines: list[str]
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not self.lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "path": str(self.path),
            "start": self.start,
            "end": self.end,
            "total_lines": self.total,
            "truncated": self.truncated,
            "lines": [
                {"number": number, "text": text}
                for number, text in zip(
                    range(self.start, self.start + len(self.lines)), self.lines
                )
            ],
        }


def looks_like_path(target: str) -> bool:
    """True when ``read`` should treat ``target`` as a file, not a symbol id."""
    text = (target or "").strip()
    if text.startswith(ID_PREFIXES):
        return False
    return any(marker in text for marker in PATH_MARKERS)


def normalise_rel_path(raw: str) -> str:
    """Validate a user-typed path and return it as ``a/b/c.go``."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        raise UsageError("no file given")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise SecurityError(
            f"absolute paths are not allowed: {raw}",
            hint="pass a path relative to the project root, exactly as stored in the index",
        )
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise SecurityError(
            f"path traversal is not allowed: {raw}",
            hint="only files inside the project root can be read",
        )
    if not parts:
        raise UsageError(f"not a file path: {raw}")
    return "/".join(parts)


def splitting_range(target: str) -> tuple[str, int | None, int | None]:
    """Split ``path.go:12-34`` into the path plus an explicit line range.

    The index prints every location as ``file.go:120-148``, so ``read`` accepts
    that same spelling: a location copied out of ``overview`` or a package page
    can be pasted straight into ``read``.  Symbol ids are left alone -- their
    trailing part (``...:memory.Get``) is never a line range -- as is anything
    else that does not end in ``:N`` or ``:N-M``.
    """
    text = (target or "").strip()
    head, separator, tail = text.rpartition(":")
    if not separator or not head or not tail:
        return text, None, None
    first, dash, last = tail.partition("-")
    if not first.isdigit() or (dash and not last.isdigit()):
        return text, None, None
    return head, int(first), int(last) if dash else None


def resolve_source_path(config: Config, raw: str) -> tuple[str, Path]:
    """The relative name and fully resolved path of a readable file."""
    rel_path = normalise_rel_path(raw)
    candidate = config.project_root.joinpath(*rel_path.split("/"))
    try:
        resolved = candidate.resolve()
    except OSError as error:
        raise SourceReadError(f"cannot resolve {rel_path}: {error}") from error
    root = config.project_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise SecurityError(
            f"{rel_path} resolves outside the project root ({resolved})",
            hint="the file is a symlink pointing out of the tree, or the path is wrong",
        )
    if not resolved.is_file():
        raise SourceReadError(
            f"no such file: {rel_path}",
            hint=f"expected an indexed .go file under {config.project_root}",
        )
    return rel_path, resolved


def read_slice(
    config: Config,
    raw: str,
    *,
    start: int | None = None,
    end: int | None = None,
    context: int = 0,
    max_lines: int = DEFAULT_READ_LINES,
) -> SourceSlice:
    """Read ``start``..``end`` of a file (1-based, inclusive) plus ``context``.

    ``end=None`` means "to the end of the file".  The file is streamed once:
    lines outside the window are counted but never kept, so the reported total
    is exact without holding the whole file in memory.
    """
    rel_path, path = resolve_source_path(config, raw)
    if context < 0:
        raise UsageError("--context cannot be negative")
    if start is not None and start < 1:
        raise UsageError("--start is 1-based and must be >= 1")
    if end is not None and end < 1:
        raise UsageError("--end is 1-based and must be >= 1")

    first = start or 1
    last = end if end is not None else None
    if last is not None and last < first:
        raise UsageError(f"--end ({last}) is before --start ({first})")
    window_start = max(1, first - context)
    window_end = None if last is None else last + context
    capacity = max_lines if max_lines > 0 else None

    collected: list[str] = []
    total = 0
    truncated = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for number, raw_line in enumerate(handle, start=1):
                total = number
                if number < window_start:
                    continue
                if window_end is not None and number > window_end:
                    continue  # past the window: keep counting, keep nothing
                if capacity is not None and len(collected) >= capacity:
                    truncated = True
                    continue
                collected.append(raw_line.rstrip("\n").rstrip("\r"))
    except OSError as error:
        raise SourceReadError(f"cannot read {rel_path}: {error}") from error

    in_range_end = window_start + len(collected) - 1
    out_of_range = not collected and total > 0 and window_start > total
    return SourceSlice(
        file=rel_path,
        path=path,
        start=window_start,
        end=total if out_of_range else in_range_end,
        total=total,
        lines=collected,
        truncated=truncated,
    )

