"""Where the index lives: project root, index directory, and the paths within.

Resolution order for the index directory (first match wins):

1. ``--index DIR`` on the command line;
2. the ``AINDEX_INDEX_DIR`` environment variable;
3. ``<project root>/.ai-index``.

A relative ``--index`` value (and a relative environment variable) is resolved
against the *project root*, matching the ``-o`` convention of the indexer.  The
project root itself comes from ``--root`` and defaults to the current directory,
so the CLI works both from inside the project and from anywhere with ``--root``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .constants import (
    DEFAULT_INDEX_DIRNAME,
    FILES_FILENAME,
    INDEX_DIR_ENV,
    PACKAGES_DIRNAME,
    PROJECT_FILENAME,
    STATS_FILENAME,
    SYMBOLS_FILENAME,
)
from .errors import IndexNotFoundError, UsageError


@dataclass(slots=True)
class Config:
    """Resolved locations the read-only commands work against."""

    project_root: Path
    index_dir: Path
    #: True when the location came from ``--index`` / ``AINDEX_INDEX_DIR``.
    explicit_index: bool = False

    @property
    def project_root_str(self) -> str:
        return str(self.project_root)

    @property
    def index_dir_str(self) -> str:
        return str(self.index_dir)

    # -- paths -------------------------------------------------------------

    def _under_index(self, *names: str) -> Path:
        return self.index_dir.joinpath(*names)

    @property
    def symbols_path(self) -> Path:
        return self._under_index(SYMBOLS_FILENAME)

    @property
    def files_path(self) -> Path:
        return self._under_index(FILES_FILENAME)

    @property
    def stats_path(self) -> Path:
        return self._under_index(STATS_FILENAME)

    @property
    def project_path(self) -> Path:
        return self._under_index(PROJECT_FILENAME)

    @property
    def packages_dir(self) -> Path:
        return self._under_index(PACKAGES_DIRNAME)

    def package_page(self, slug: str) -> Path:
        return self.packages_dir / f"{slug}.md"

    # -- validation --------------------------------------------------------

    def exists(self) -> bool:
        """True when the index directory looks healthy enough to use."""
        return self.index_dir.is_dir() and self.symbols_path.is_file()

    def require_index(self) -> "Config":
        """Raise a typed error explaining exactly what is missing."""
        if not self.index_dir.exists():
            raise IndexNotFoundError(
                str(self.index_dir),
                message=f"index directory not found: {self.index_dir}",
                hint=(
                    "build the index first: "
                    f"python3 aindex-make {self.project_root}"
                ),
            )
        if not self.index_dir.is_dir():
            raise IndexNotFoundError(
                str(self.index_dir),
                message=f"not a directory: {self.index_dir}",
            )
        if not self.symbols_path.is_file():
            raise IndexNotFoundError(
                str(self.index_dir),
                message=f"missing {SYMBOLS_FILENAME} in {self.index_dir}",
                hint=(
                    "the index is incomplete; regenerate it with "
                    f"python3 aindex-make {self.project_root}"
                ),
            )
        return self


def resolve_config(
    *,
    root: str | None = None,
    index: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Turn ``--root`` / ``--index`` / the environment into a :class:`Config`."""
    env = os.environ if environ is None else environ

    raw_root = root or "."
    project_root = Path(raw_root).expanduser()
    try:
        project_root = project_root.resolve()
    except OSError as error:  # pragma: no cover - only on pathological inputs
        raise UsageError(f"cannot resolve project root {raw_root!r}: {error}") from error

    raw_index = index if index is not None else env.get(INDEX_DIR_ENV, "")
    explicit = bool(raw_index)
    if raw_index:
        candidate = Path(raw_index).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
    else:
        candidate = project_root / DEFAULT_INDEX_DIRNAME
    try:
        index_dir = candidate.resolve()
    except OSError as error:  # pragma: no cover
        raise UsageError(f"cannot resolve index directory {raw_index!r}: {error}") from error

    return Config(project_root=project_root, index_dir=index_dir, explicit_index=explicit)
