"""Streaming, cached loader for an existing ``.ai-index`` directory.

One :class:`IndexStore` is created per process and loads each JSONL file exactly
once, lazily and line by line (``symbols.jsonl`` can be large; nothing here
reads a ``.go`` file or walks the tree).  A malformed line is recorded as a
:class:`~aindex_query.models.ParseProblem` and skipped, so a single corrupt
record never takes the whole CLI down -- ``doctor`` reports the problems, every
other command simply works with the records it could read.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from .config import Config
from .constants import DEFAULT_CANDIDATE_LIMIT, SYMBOLS_FILENAME
from .errors import IndexNotFoundError, InvalidIndexError, UsageError
from .models import (
    CandidateList,
    FileRecord,
    IndexStats,
    ParseProblem,
    Symbol,
)

T = TypeVar("T")


@dataclass(slots=True)
class PackageGroup:
    """One package: its files, its symbols and its package-level doc."""

    package_path: str
    name: str
    purpose: str = ""
    directory: str = ""
    files: list[FileRecord] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    record: Symbol | None = None
    test_package: bool = False

    @property
    def key(self) -> str:
        return f"{self.package_path}|{self.name}"

    def symbols_of(self, kinds: Iterable[str], *, exported_only: bool = False) -> list[Symbol]:
        """Symbols of the wanted kinds, in stable order."""
        wanted = set(kinds)
        chosen = [s for s in self.symbols if s.kind in wanted]
        if exported_only:
            chosen = [s for s in chosen if s.exported]
        return sorted(chosen, key=Symbol.sort_key)

    def sort_key(self) -> tuple[object, ...]:
        segments = tuple(self.directory.split("/")) if self.directory else ()
        return (segments, self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_path": self.package_path,
            "name": self.name,
            "directory": self.directory,
            "files": len(self.files),
            "symbols": len(self.symbols),
            "purpose": self.purpose,
            "test_package": self.test_package,
        }


def _read_jsonl(
    path: Path, factory: Callable[[Mapping[str, Any]], T]
) -> tuple[list[T], list[ParseProblem]]:
    """Parse ``path`` as JSON Lines; returns the records and the bad lines."""
    records: list[T] = []
    problems: list[ParseProblem] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                except json.JSONDecodeError as error:
                    problems.append(
                        ParseProblem(str(path), number, f"invalid JSON: {error.msg}")
                    )
                    continue
                if not isinstance(raw, dict):
                    problems.append(
                        ParseProblem(str(path), number, "record is not a JSON object")
                    )
                    continue
                records.append(factory(raw))
    except OSError as error:
        raise InvalidIndexError(
            f"cannot read {path}: {error.strerror or error}",
            hint=(
                "the index is unreadable; regenerate it with "
                "python3 aindex-make <project root>"
            ),
        ) from error
    return records, problems


def _dedupe_by_id(symbols: Iterable[Symbol]) -> list[Symbol]:
    """Keep the first occurrence of each symbol id, order preserved."""
    seen: dict[str, Symbol] = {}
    for symbol in symbols:
        seen.setdefault(symbol.id, symbol)
    return list(seen.values())


class IndexStore:
    """Read-only access to one ``.ai-index`` directory."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._symbols: list[Symbol] | None = None
        self._files: list[FileRecord] | None = None
        self._stats: IndexStats | None = None
        self._stats_missing = False
        self._problems: list[ParseProblem] = []
        self._by_id: dict[str, Symbol] | None = None
        self._by_qualified: dict[str, list[Symbol]] | None = None
        self._by_name: dict[str, list[Symbol]] | None = None
        self._by_package_path: dict[str, list[Symbol]] | None = None
        self._files_by_path: dict[str, FileRecord] | None = None
        self._groups: list[PackageGroup] | None = None
        self._callers_index: dict[str, list[str]] | None = None
        self._dirs: dict[str, list[str]] | None = None

    # -- lazy loading ------------------------------------------------------

    @property
    def symbols(self) -> list[Symbol]:
        if self._symbols is None:
            self._load_symbols()
        assert self._symbols is not None
        return self._symbols

    @property
    def files(self) -> list[FileRecord]:
        if self._files is None:
            path = self.config.files_path
            if path.is_file():
                records, problems = _read_jsonl(path, FileRecord.from_dict)
            else:
                records, problems = [], []
            self._files = records
            self._problems.extend(problems)
        return self._files

    @property
    def stats(self) -> IndexStats:
        if self._stats is None:
            path = self.config.stats_path
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        raw = json.load(handle)
                except (OSError, json.JSONDecodeError) as error:
                    self._problems.append(
                        ParseProblem(str(path), 0, f"unreadable stats: {error}")
                    )
                    raw = {}
                if not isinstance(raw, dict):
                    self._problems.append(
                        ParseProblem(str(path), 0, "stats is not a JSON object")
                    )
                    raw = {}
                self._stats = IndexStats.from_dict(raw)
            else:
                self._stats_missing = True
                self._stats = self._derive_stats()
        return self._stats

    @property
    def stats_missing(self) -> bool:
        """True when ``stats.json`` was absent and the stats were derived."""
        self.stats  # noqa: B018 - force the lazy load
        return self._stats_missing

    @property
    def problems(self) -> list[ParseProblem]:
        """Malformed JSONL lines from both files (forcing a full load)."""
        self.symbols, self.files, self.stats  # noqa: B018
        return list(self._problems)

    def _load_symbols(self) -> None:
        path = self.config.symbols_path
        if not path.is_file():
            raise IndexNotFoundError(
                str(self.config.index_dir),
                message=f"missing {SYMBOLS_FILENAME} in {self.config.index_dir}",
                hint=(
                    "the index is incomplete; regenerate it with "
                    f"python3 aindex-make {self.config.project_root}"
                ),
            )
        records, problems = _read_jsonl(path, Symbol.from_dict)
        self._symbols = records
        self._problems.extend(problems)

    def _derive_stats(self) -> IndexStats:
        """Fallback stats when ``stats.json`` is missing: derive from the JSONL."""
        symbols = self.symbols
        files = self.files
        resolved = sum(len(s.calls) for s in symbols)
        unresolved = sum(len(s.unresolved_calls) for s in symbols)
        return IndexStats(
            generated_at="",
            root=self.config.project_root_str,
            packages=len({s.package_path for s in symbols if s.is_package}),
            files=len(files),
            symbols=len([s for s in symbols if not s.is_package]),
            calls=resolved + unresolved,
            resolved_calls=resolved,
            unresolved_calls=unresolved,
            parse_errors=len([f for f in files if f.parse_error]),
            backend="derived",
        )

    # -- indexes -----------------------------------------------------------

    @property
    def by_id(self) -> dict[str, Symbol]:
        if self._by_id is None:
            self._by_id = {symbol.id: symbol for symbol in self.symbols}
        return self._by_id

    def _grouped(self, attr: str) -> dict[str, list[Symbol]]:
        grouped: dict[str, list[Symbol]] = {}
        for symbol in self.symbols:
            value = getattr(symbol, attr)
            if value:
                grouped.setdefault(value, []).append(symbol)
        for values in grouped.values():
            values.sort(key=Symbol.sort_key)
        return grouped

    @property
    def by_qualified(self) -> dict[str, list[Symbol]]:
        if self._by_qualified is None:
            self._by_qualified = self._grouped("qualified_name")
        return self._by_qualified

    @property
    def by_name(self) -> dict[str, list[Symbol]]:
        if self._by_name is None:
            self._by_name = self._grouped("name")
        return self._by_name

    @property
    def by_package_path(self) -> dict[str, list[Symbol]]:
        if self._by_package_path is None:
            self._by_package_path = self._grouped("package_path")
        return self._by_package_path

    @property
    def files_by_path(self) -> dict[str, FileRecord]:
        if self._files_by_path is None:
            self._files_by_path = {record.file: record for record in self.files}
        return self._files_by_path

    # -- symbol resolution -------------------------------------------------

    @property
    def package_paths(self) -> list[str]:
        """Known package paths, longest first (best dotted-prefix match)."""
        return sorted({s.package_path for s in self.symbols if s.package_path}, key=len, reverse=True)

    def resolve(self, query: str, *, limit: int = DEFAULT_CANDIDATE_LIMIT) -> CandidateList:
        """Turn a user-typed reference into exactly one symbol or a candidate list.

        Each attempt below is tried in order and the first non-empty pool wins: if
        it holds exactly one symbol the reference is resolved, otherwise the pool
        is handed back as candidates and the CLI exits non-zero listing their ids.
        Nothing is ever guessed.

        Accepted forms: full ``id``, ``qualified_name`` (``Type.Method``),
        ``<import path>.<qualified name>``, ``<file path>.<qualified name>``,
        bare ``name``, all three retried case-insensitively, then
        ``<package name>.<qualified name>``, and finally a prefix/substring scan.
        """
        text = query.strip()
        if not text:
            raise UsageError("empty symbol reference")
        lowered = text.lower()
        attempts: tuple[Iterable[Symbol], ...] = (
            self._pool_by_id(text),
            self._pool_by_qualified(text),
            self._pool_by_dotted_path(text),
            self._pool_by_dotted_directory(text),
            self._pool_by_name(text),
            self._pool_by_identity_ignore_case(lowered),
            self._pool_by_name_ignore_case(lowered),
            self._pool_by_package_name(text),
            self._pool_loose(lowered),
        )
        for attempt in attempts:
            pool = self._clean_pool(attempt)
            if not pool:
                continue
            if len(pool) == 1:
                return CandidateList(query=query, symbol=pool[0])
            truncated = False
            if limit > 0 and len(pool) > limit:
                pool = pool[:limit]
                truncated = True
            return CandidateList(query=query, candidates=pool, truncated=truncated)
        return CandidateList(query=query)

    def _clean_pool(self, pool: Iterable[Symbol]) -> list[Symbol]:
        """Deduplicate by id and order deterministically."""
        return self._sort_symbols(_dedupe_by_id(pool))

    def _pool_by_id(self, text: str) -> list[Symbol]:
        symbol = self.by_id.get(text)
        return [symbol] if symbol is not None else []

    def _pool_by_qualified(self, text: str) -> list[Symbol]:
        return list(self.by_qualified.get(text, ()))

    def _pool_by_name(self, text: str) -> list[Symbol]:
        return list(self.by_name.get(text, ()))

    def _pool_by_dotted_path(self, text: str) -> list[Symbol]:
        """``<import path>.<qualified name>``: the first package that fits wins."""
        for path in self.package_paths:
            prefix = f"{path}."
            if not text.startswith(prefix):
                continue
            wanted = text[len(prefix) :]
            hits = [
                s for s in self.by_package_path.get(path, ()) if s.qualified_name == wanted
            ]
            if hits:
                return hits
        return []

    def _pool_by_dotted_directory(self, text: str) -> list[Symbol]:
        """``<directory>.<qualified name>``, using the dirs shown in PROJECT.md."""
        directory, dot, wanted = text.partition(".")
        if not dot or not directory or not wanted:
            return []
        paths = self.packages_by_directory.get(directory)
        if not paths:
            return []
        hits: list[Symbol] = []
        for path in paths:
            hits.extend(
                s for s in self.by_package_path.get(path, ()) if s.qualified_name == wanted
            )
        return hits

    def _pool_by_identity_ignore_case(self, lowered: str) -> list[Symbol]:
        return [
            s
            for s in self.symbols
            if lowered in (s.id.lower(), s.qualified_name.lower())
        ]

    def _pool_by_name_ignore_case(self, lowered: str) -> list[Symbol]:
        return [s for s in self.symbols if s.name.lower() == lowered]

    def _pool_by_package_name(self, text: str) -> list[Symbol]:
        """``<package name>.<qualified name>``, e.g. ``tunnel.peekClosed``."""
        name, dot, wanted = text.partition(".")
        if not dot or not name or not wanted:
            return []
        paths = sorted({g.package_path for g in self.package_groups if g.name == name})
        hits: list[Symbol] = []
        for path in paths:
            hits.extend(
                s for s in self.by_package_path.get(path, ()) if s.qualified_name == wanted
            )
        return hits

    def _pool_loose(self, lowered: str) -> list[Symbol]:
        """Prefix scan, then a substring scan: the last resort before "not found"."""
        starts: list[Symbol] = []
        contains: list[Symbol] = []
        for symbol in self.symbols:
            name = symbol.name.lower()
            qualified = symbol.qualified_name.lower()
            identifier = symbol.id.lower()
            if (
                name.startswith(lowered)
                or qualified.startswith(lowered)
                or identifier.startswith(lowered)
            ):
                starts.append(symbol)
            elif lowered in name or lowered in qualified or lowered in identifier:
                contains.append(symbol)
        return starts or contains

    @staticmethod
    def _sort_symbols(symbols: Iterable[Symbol]) -> list[Symbol]:
        return sorted(symbols, key=Symbol.sort_key)

    def find_symbols(self, ids: Sequence[str]) -> tuple[list[Symbol], list[str]]:
        """Split ids into the ones that exist and the ones that do not."""
        found: list[Symbol] = []
        missing: list[str] = []
        seen: set[str] = set()
        for identifier in ids:
            token = identifier.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            symbol = self.by_id.get(token)
            if symbol is None:
                missing.append(token)
            else:
                found.append(symbol)
        return found, missing

    # -- packages ----------------------------------------------------------

    @property
    def package_groups(self) -> list[PackageGroup]:
        """All packages, folders before files, deterministic order."""
        if self._groups is None:
            self._groups = self._build_groups()
        return self._groups

    def _build_groups(self) -> list[PackageGroup]:
        """One group per ``(directory, package name)`` pair.

        Symbols and files of the same package are merged even when the indexer
        reported different ``package_path`` values for them: an external test
        package (``package api_test`` living in ``internal/api``) is written with
        the import path of its *directory* on the file record, but with its own
        ``_test`` path (``.../internal/api_test``) on the package record.
        """
        groups: list[PackageGroup] = []
        by_name: dict[str, list[PackageGroup]] = {}

        def group_for(package_path: str, name: str, directory: str) -> PackageGroup:
            candidates = by_name.get(name, [])
            group = next(
                (item for item in candidates if item.directory == directory), None
            )
            if group is None and directory:
                # A package record carries no file, hence no directory: let it
                # adopt the directory of the symbols and files that do.
                group = next((item for item in candidates if not item.directory), None)
                if group is not None:
                    group.directory = directory
            if group is None and not directory and candidates:
                group = candidates[0]
            if group is None:
                group = PackageGroup(
                    package_path=package_path, name=name, directory=directory
                )
                groups.append(group)
                by_name.setdefault(name, []).append(group)
            return group

        for symbol in self.symbols:
            group = group_for(
                symbol.package_path, symbol.package, os.path.dirname(symbol.file)
            )
            if symbol.is_package:
                group.record = symbol
                group.purpose = symbol.doc
                group.test_package = symbol.test_related
            else:
                group.symbols.append(symbol)

        for record in self.files:
            group = group_for(record.package_path, record.package, record.directory)
            group.files.append(record)
            if record.test_related:
                group.test_package = True

        for group in groups:
            group.symbols.sort(key=Symbol.sort_key)
            group.files.sort(key=lambda item: item.file)
            if not group.directory:
                group.directory = _group_directory(group)
        return sorted(groups, key=PackageGroup.sort_key)

    @property
    def packages_by_directory(self) -> dict[str, list[str]]:
        """``directory -> package paths``, for the ``<dir>.<Name>`` query form."""
        if self._dirs is None:
            mapping: dict[str, list[str]] = {}
            for group in self.package_groups:
                if not group.directory:
                    continue
                bucket = mapping.setdefault(group.directory, [])
                if group.package_path not in bucket:
                    bucket.append(group.package_path)
            for bucket in mapping.values():
                bucket.sort()
            self._dirs = mapping
        return self._dirs

    def group_for_path(self, package_path: str, name: str = "") -> PackageGroup | None:
        for group in self.package_groups:
            if group.package_path == package_path and (not name or group.name == name):
                return group
        return None

    def find_package(self, query: str) -> list[PackageGroup]:
        """Resolve ``--package`` style input to package groups (path or name)."""
        text = query.strip().strip("/")
        if not text:
            return []
        exact = [g for g in self.package_groups if text in (g.package_path, g.name)]
        if exact:
            return exact
        suffix = [g for g in self.package_groups if g.package_path.endswith(f"/{text}")]
        if suffix:
            return suffix
        lowered = text.lower()
        return [
            g
            for g in self.package_groups
            if lowered in g.package_path.lower() or lowered in g.name.lower()
        ]

    # -- call graph --------------------------------------------------------

    @property
    def callers_index(self) -> dict[str, list[str]]:
        """``target id -> caller ids``, built once from the ``calls`` field."""
        index = self._callers_index
        if index is None:
            index = {}
            for symbol in self.symbols:
                for target in symbol.calls:
                    bucket = index.setdefault(target, [])
                    if symbol.id not in bucket:
                        bucket.append(symbol.id)
            for ids in index.values():
                ids.sort()
            self._callers_index = index
        return index

    def callers_of(self, symbol_id: str) -> list[Symbol]:
        ids = self.callers_index.get(symbol_id, [])
        found, _ = self.find_symbols(ids)
        return found

    def callees_of(self, symbol: Symbol) -> tuple[list[Symbol], list[str]]:
        """Callees that exist in the index, plus ids that point nowhere.

        The second element is *not* ``symbol.unresolved_calls``: raw call names
        never became ids, while these did and simply are missing from this index
        (a stale or filtered index).  They are reported as dangling ids.
        """
        return self.find_symbols(symbol.calls)

    # -- integrity ---------------------------------------------------------

    def dangling_references(self, limit: int = 0) -> tuple[int, list[tuple[str, str, str]]]:
        """Count and sample ``calls``/``callers`` pointing at unknown ids."""
        count = 0
        samples: list[tuple[str, str, str]] = []

        def note(origin: str, field_name: str, target: str) -> None:
            nonlocal count
            count += 1
            if limit <= 0 or len(samples) < limit:
                samples.append((origin, field_name, target))

        for symbol in self.symbols:
            for target in symbol.calls:
                if target not in self.by_id:
                    note(symbol.id, "calls", target)
            for target in symbol.callers:
                if target not in self.by_id:
                    note(symbol.id, "callers", target)
        return count, samples

    def index_age_seconds(self) -> float | None:
        """Seconds since the index was written, or ``None`` if unknowable."""
        generated = self.stats.generated_at
        if not generated:
            return None
        try:
            stamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds()

    def stale_files(self, *, limit: int = 20) -> list[str]:
        """Indexed ``.go`` files modified after ``stats.json`` was written."""
        reference = self.config.stats_path
        if not reference.is_file():
            return []
        try:
            baseline = reference.stat().st_mtime
        except OSError:  # pragma: no cover - races only
            return []
        stale: list[str] = []
        for record in self.files:
            candidate = self.config.project_root / record.file
            try:
                if candidate.is_file() and candidate.stat().st_mtime > baseline:
                    stale.append(record.file)
            except OSError:  # pragma: no cover
                continue
            if limit > 0 and len(stale) >= limit:
                break
        return stale


def _group_directory(group: PackageGroup) -> str:
    """Best-effort on-disk directory of a package, for display only."""
    directories = [record.directory for record in group.files if record.directory]
    if directories:
        directories.sort(key=lambda item: (item.count("/"), item))
        return directories[0]
    for symbol in group.symbols:
        if symbol.file and "/" in symbol.file:
            return os.path.dirname(symbol.file)
    return ""




