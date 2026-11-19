"""Writing the machine-readable part of the index.

Only the standard library is used.  Every file is written *atomically* (temporary
file plus :func:`os.replace`) so that a killed run never leaves a half-written
index behind, and re-running the tool is idempotent.

The layout below is the contract an agent (or a small script) relies on::

    .ai-index/
      PROJECT.md      overview and navigation (see :mod:`aindex.render`)
      packages/*.md   one page per package
      symbols.jsonl   one JSON object per symbol
      files.jsonl     one JSON object per Go file
      stats.json      run statistics
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Protocol

from .constants import (
    FILES_FILENAME,
    STATS_FILENAME,
    SYMBOLS_FILENAME,
)
from .model import FileRecord, Stats, SymbolRecord


class JsonRecord(Protocol):
    """Anything that knows how to serialize itself to one JSONL line."""

    def to_json(self) -> str:  # pragma: no cover - protocol definition
        ...


def ensure_directory(path: str) -> str:
    """Create ``path`` (and parents) if needed and return it."""
    os.makedirs(path, exist_ok=True)
    return path


def write_text(path: str, text: str) -> str:
    """Write ``text`` as UTF-8 with ``\\n`` endings, atomically."""
    ensure_directory(os.path.dirname(path) or ".")
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)
    return path


def write_json(path: str, payload: Any) -> str:
    """Write ``payload`` as pretty-printed JSON with a trailing newline."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return write_text(path, text)


def write_jsonl(path: str, records: Iterable[JsonRecord]) -> int:
    """Write ``records`` as JSON Lines; returns the number of lines written."""
    lines = [record.to_json() for record in records]
    body = "".join(line + "\n" for line in lines)
    write_text(path, body)
    return len(lines)


def write_symbols(index_dir: str, symbols: Iterable[SymbolRecord]) -> str:
    """Write ``symbols.jsonl``; returns the file path."""
    return write_text(
        os.path.join(index_dir, SYMBOLS_FILENAME),
        "".join(f"{symbol.to_json()}\n" for symbol in symbols),
    )


def write_files(index_dir: str, files: Iterable[FileRecord]) -> str:
    """Write ``files.jsonl``; returns the file path."""
    return write_text(
        os.path.join(index_dir, FILES_FILENAME),
        "".join(f"{go_file.to_json()}\n" for go_file in files),
    )


def write_stats(index_dir: str, stats: Stats) -> str:
    """Write ``stats.json``; returns the file path."""
    return write_json(os.path.join(index_dir, STATS_FILENAME), stats.to_dict())
