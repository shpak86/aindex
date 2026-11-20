"""Typed, tolerant view of the records written by the ``aindex`` indexer.

The dataclasses mirror ``symbols.jsonl`` / ``files.jsonl`` / ``stats.json`` one
field per key, but every ``from_dict`` tolerates a slightly different index:
unknown keys are ignored, missing keys fall back to a neutral value and a wrong
type never raises.  A broken line is the loader's problem
(:mod:`aindex_query.index_store`), not a reason for a command to crash.

The only additions over the raw records are read-only convenience properties
(``location``, ``doc_line``) -- the CLI never invents data, it only reformats
what the indexer already decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .constants import DOC_PREVIEW_CHARS, KIND_ORDER


def _text(value: Any) -> str:
    """Coerce to ``str``; anything that is not a string becomes ``""``."""
    return value if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    return bool(value)


def _number(value: Any) -> int:
    """Integers stay as they are; ``bool`` (an ``int`` subclass) does not count."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _ratio(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _strings(value: Any) -> list[str]:
    """A list of non-empty strings; anything else becomes an empty list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _fold(doc: str, width: int = DOC_PREVIEW_CHARS) -> str:
    """Fold a doc comment onto one line, verbatim, cut at ``width``."""
    text = " ".join(doc.split())
    if width > 0 and len(text) > width:
        return text[: width - 3].rstrip() + "..."
    return text


@dataclass(slots=True)
class Symbol:
    """One record of ``symbols.jsonl``."""

    id: str
    kind: str
    package: str
    package_path: str
    file: str
    name: str
    qualified_name: str
    receiver: str
    receiver_type: str
    signature: str
    doc: str
    exported: bool
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    calls: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    unresolved_calls: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    test_related: bool = False
    content_hash: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Symbol":
        return cls(
            id=_text(raw.get("id")),
            kind=_text(raw.get("kind")),
            package=_text(raw.get("package")),
            package_path=_text(raw.get("package_path")),
            file=_text(raw.get("file")),
            name=_text(raw.get("name")),
            qualified_name=_text(raw.get("qualified_name")),
            receiver=_text(raw.get("receiver")),
            receiver_type=_text(raw.get("receiver_type")),
            signature=_text(raw.get("signature")),
            doc=_text(raw.get("doc")),
            exported=_flag(raw.get("exported")),
            start_line=_number(raw.get("start_line")),
            end_line=_number(raw.get("end_line")),
            start_byte=_number(raw.get("start_byte")),
            end_byte=_number(raw.get("end_byte")),
            calls=_strings(raw.get("calls")),
            callers=_strings(raw.get("callers")),
            unresolved_calls=_strings(raw.get("unresolved_calls")),
            types=_strings(raw.get("types")),
            fields=_strings(raw.get("fields")),
            methods=_strings(raw.get("methods")),
            imports=_strings(raw.get("imports")),
            test_related=_flag(raw.get("test_related")),
            content_hash=_text(raw.get("content_hash")),
        )

    # -- derived view ------------------------------------------------------

    @property
    def is_package(self) -> bool:
        return self.kind == "package"

    @property
    def display_name(self) -> str:
        """``Type.Method`` for methods, the bare name otherwise."""
        return self.qualified_name or self.name

    @property
    def location(self) -> str:
        """``file:start-end`` -- empty for a package record."""
        if not self.file:
            return ""
        if self.start_line and self.end_line:
            return f"{self.file}:{self.start_line}-{self.end_line}"
        return self.file

    @property
    def doc_line(self) -> str:
        """The doc comment folded to a single, printable line."""
        return _fold(self.doc)

    @property
    def kind_rank(self) -> int:
        try:
            return KIND_ORDER.index(self.kind)
        except ValueError:
            return len(KIND_ORDER)

    def sort_key(self) -> tuple[Any, ...]:
        """Stable ordering: exported first, then kind, name and id."""
        return (not self.exported, self.kind_rank, self.qualified_name.casefold(), self.id)

    def to_dict(self) -> dict[str, Any]:
        """The full record, in the documented key order of ``symbols.jsonl``."""
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
            "calls": list(self.calls),
            "callers": list(self.callers),
            "types": list(self.types),
            "fields": list(self.fields),
            "methods": list(self.methods),
            "imports": list(self.imports),
            "test_related": self.test_related,
            "content_hash": self.content_hash,
            "unresolved_calls": list(self.unresolved_calls),
        }

    def card(self) -> dict[str, Any]:
        """The compact projection used in listings and search results."""
        return {
            "id": self.id,
            "kind": self.kind,
            "package": self.package,
            "package_path": self.package_path,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "signature": self.signature,
            "doc": self.doc,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "exported": self.exported,
            "test_related": self.test_related,
        }


@dataclass(slots=True)
class FileRecord:
    """One record of ``files.jsonl``."""

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

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FileRecord":
        return cls(
            file=_text(raw.get("file")),
            package=_text(raw.get("package")),
            package_path=_text(raw.get("package_path")),
            lines=_number(raw.get("lines")),
            symbols=_number(raw.get("symbols")),
            imports=_strings(raw.get("imports")),
            build_tags=_strings(raw.get("build_tags")),
            test_related=_flag(raw.get("test_related")),
            parse_error=_flag(raw.get("parse_error")),
            error=_text(raw.get("error")),
            content_hash=_text(raw.get("content_hash")),
        )

    @property
    def directory(self) -> str:
        """Directory holding the file, relative to the project root."""
        head, _, _ = self.file.rpartition("/")
        return head

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "package": self.package,
            "package_path": self.package_path,
            "lines": self.lines,
            "symbols": self.symbols,
            "imports": list(self.imports),
            "build_tags": list(self.build_tags),
            "test_related": self.test_related,
            "parse_error": self.parse_error,
            "error": self.error,
            "content_hash": self.content_hash,
        }


@dataclass(slots=True)
class IndexStats:
    """Contents of ``stats.json``."""

    generated_at: str = ""
    root: str = ""
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

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IndexStats":
        return cls(
            generated_at=_text(raw.get("generated_at")),
            root=_text(raw.get("root")),
            module_path=_text(raw.get("module_path")),
            go_version=_text(raw.get("go_version")),
            packages=_number(raw.get("packages")),
            files=_number(raw.get("files")),
            symbols=_number(raw.get("symbols")),
            calls=_number(raw.get("calls")),
            resolved_calls=_number(raw.get("resolved_calls")),
            unresolved_calls=_number(raw.get("unresolved_calls")),
            parse_errors=_number(raw.get("parse_errors")),
            duration_seconds=_ratio(raw.get("duration_seconds")),
            backend=_text(raw.get("backend")),
            ignored_files=_number(raw.get("ignored_files")),
        )

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
            "duration_seconds": self.duration_seconds,
            "backend": self.backend,
            "ignored_files": self.ignored_files,
        }


@dataclass(slots=True)
class SearchResult:
    """A symbol plus the deterministic score it was ranked by."""

    symbol: Symbol
    score: float

    def to_dict(self) -> dict[str, Any]:
        payload = self.symbol.card()
        payload["score"] = round(self.score, 4)
        return payload


@dataclass(slots=True)
class ParseProblem:
    """One malformed JSONL line; the rest of the file still loads."""

    path: str
    line: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.path, "line": self.line, "error": self.message}


@dataclass(slots=True)
class CandidateList:
    """Outcome of resolving a user-typed symbol reference."""

    query: str
    symbol: Symbol | None = None
    candidates: list[Symbol] = field(default_factory=list)
    truncated: bool = False

    @property
    def found(self) -> bool:
        return self.symbol is not None

    @property
    def ambiguous(self) -> bool:
        return self.symbol is None and bool(self.candidates)

