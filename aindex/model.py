"""Index data model.

Plain :mod:`dataclasses` are used on purpose: the index is written to disk
immediately, so validation beyond what the parser guarantees is not needed and
``pydantic`` would only add a dependency.

All records are intentionally *flat and string based*: the index file is the
machine-readable source of truth for an AI agent, and flat records are cheap to
grep and to stream line-by-line.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from .constants import DOC_TRUNCATION_SUFFIX, KIND_PACKAGE


def content_hash(data: bytes) -> str:
    """Return a stable content hash for change detection."""
    return hashlib.sha256(data).hexdigest()


def truncate_text(text: str, limit: int, suffix: str = DOC_TRUNCATION_SUFFIX) -> str:
    """Truncate ``text`` to ``limit`` characters, marking the cut with ``suffix``."""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


@dataclass(slots=True)
class ImportSpec:
    """One entry of an ``import`` declaration."""

    path: str
    alias: str = ""
    line: int = 0

    @property
    def local_name(self) -> str:
        """Name used to reference the package: alias, ``.``/``_`` or last path segment."""
        if self.alias:
            return self.alias
        return self.path.rsplit("/", 1)[-1]

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "alias": self.alias, "line": self.line}


@dataclass(slots=True)
class SymbolRecord:
    """One indexed symbol.  Serialized as a single JSONL line."""

    id: str
    kind: str
    package: str
    package_path: str
    file: str
    name: str
    qualified_name: str
    receiver: str
    signature: str
    doc: str
    exported: bool
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    calls: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    #: Base type name of a method receiver (``memory`` for ``func (m *memory)``).
    receiver_type: str = ""
    test_related: bool = False
    content_hash: str = ""
    #: Extra, non-spec key: raw callee names that could not be resolved.
    unresolved_calls: list[str] = field(default_factory=list)

    @property
    def is_package(self) -> bool:
        return self.kind == KIND_PACKAGE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict with the documented key order."""
        return {
            "id": self.id,
            "kind": self.kind,
            "package": self.package,
            "package_path": self.package_path,
            "file": self.file,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "receiver": self.receiver,
            "receiver_type": self.receiver_type,
            "signature": self.signature,
            "doc": self.doc,
            "exported": self.exported,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "calls": self.calls,
            "callers": self.callers,
            "types": self.types,
            "fields": self.fields,
            "methods": self.methods,
            "imports": self.imports,
            "test_related": self.test_related,
            "content_hash": self.content_hash,
            "unresolved_calls": self.unresolved_calls,
        }

    def to_json(self) -> str:
        """Serialize to a single-line JSON string (JSONL record)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class FileRecord:
    """One Go source file."""

    file: str
    package: str
    package_path: str
    lines: int
    symbols: int
    imports: list[str] = field(default_factory=list)
    build_tags: list[str] = field(default_factory=list)
    test_related: bool = False
    parse_error: bool = False
    error: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "package": self.package,
            "package_path": self.package_path,
            "lines": self.lines,
            "symbols": self.symbols,
            "imports": self.imports,
            "build_tags": self.build_tags,
            "test_related": self.test_related,
            "parse_error": self.parse_error,
            "error": self.error,
            "content_hash": self.content_hash,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class PackageInfo:
    """A Go package (one directory plus one package clause)."""

    path: str
    name: str
    directory: str
    doc: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    test_related: bool = False

    @property
    def key(self) -> str:
        """Unique key: package path plus package name (``_test`` packages differ)."""
        return f"{self.path}|{self.name}"

    @property
    def is_test_package(self) -> bool:
        return self.test_related or self.name.endswith("_test")

    def symbols_of_kinds(self, kinds: Iterable[str]) -> list[SymbolRecord]:
        wanted = set(kinds)
        return [symbol for symbol in self.symbols if symbol.kind in wanted]

    def sorted_symbols(self) -> list[SymbolRecord]:
        """Exported symbols first, then by kind order, name and line."""
        from .constants import SYMBOL_KIND_ORDER

        rank = {kind: index for index, kind in enumerate(SYMBOL_KIND_ORDER)}
        return sorted(
            self.symbols,
            key=lambda s: (
                not s.exported,
                rank.get(s.kind, len(rank)),
                s.name.casefold(),
                s.start_line,
            ),
        )


@dataclass(slots=True)
class Stats:
    """Run statistics, printed at the end of a run and stored in ``stats.json``."""

    generated_at: str
    root: str
    module_path: str = ""
    go_version: str = ""
    packages: int = 0
    files: int = 0
    symbols: int = 0
    calls: int = 0
    resolved_calls: int = 0
    unresolved_calls: int = 0
    parse_errors: int = 0
    duration_seconds: float = 0.0
    backend: str = ""
    ignored_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "root": self.root,
            "module_path": self.module_path,
            "go_version": self.go_version,
            "packages": self.packages,
            "files": self.files,
            "symbols": self.symbols,
            "calls": self.calls,
            "resolved_calls": self.resolved_calls,
            "unresolved_calls": self.unresolved_calls,
            "parse_errors": self.parse_errors,
            "duration_seconds": round(self.duration_seconds, 3),
            "backend": self.backend,
            "ignored_files": self.ignored_files,
        }

    def summary_lines(self) -> list[str]:
        """Human readable summary printed by the CLI."""
        return [
            f"packages: {self.packages}",
            f"files: {self.files}",
            f"symbols: {self.symbols}",
            f"calls: {self.calls} (resolved {self.resolved_calls}, "
            f"unresolved {self.unresolved_calls})",
            f"parse errors: {self.parse_errors}",
            f"duration: {self.duration_seconds:.2f}s",
        ]


@dataclass(slots=True)
class GoModule:
    """Contents of ``go.mod`` relevant to the indexer."""

    path: str = ""
    module_path: str = ""
    go_version: str = ""


def package_order_key(package: PackageInfo) -> tuple[tuple[str, ...], str]:
    """Deterministic package order: ``a``, then ``a/b``, then ``a/b/c``.

    Sorting the path by segments (instead of as one string) keeps a package
    above its subpackages, which is how the project page is meant to read.
    """
    return (tuple(package.path.split("/")), package.name)


@dataclass(slots=True)
class IndexResult:
    """Everything produced by one indexing run."""

    root: str
    module: GoModule
    packages: dict[str, PackageInfo]
    symbols: list[SymbolRecord]
    files: list[FileRecord]
    stats: Stats

    def iter_packages(self) -> Iterator[PackageInfo]:
        """Yield packages in deterministic directory order (see :func:`package_order_key`)."""
        for package in sorted(self.packages.values(), key=package_order_key):
            yield package

    def production_packages(self) -> list[PackageInfo]:
        return [pkg for pkg in self.iter_packages() if not pkg.is_test_package]

    def to_dict(self) -> dict[str, Any]:
        """Summary of the run for printing or piping (symbols live in ``symbols.jsonl``)."""
        return {
            "root": self.root,
            "module": {
                "path": self.module.path,
                "module_path": self.module.module_path,
                "go_version": self.module.go_version,
            },
            "packages": [
                {
                    "key": package.key,
                    "path": package.path,
                    "name": package.name,
                    "directory": package.directory,
                    "files": len(package.files),
                    "symbols": len(package.symbols),
                    "test_package": package.is_test_package,
                }
                for package in self.iter_packages()
            ],
            "stats": self.stats.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize the whole result to a single-line JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
