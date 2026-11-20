"""Turning declarations into index symbols.

This is the layer between the parser (:mod:`aindex.goparse`) and the call graph
(:mod:`aindex.calls`).  It owns two conventions that the rest of the tool relies
on:

* the **symbol id** -- ``kind:package path:qualified name`` -- which is what
  ``calls``, ``callers``, ``types`` and ``methods`` refer to;
* the **member summary** of a type: struct field lines, or interface method
  specs (embedded fields are marked ``[embedded]``).
"""

from __future__ import annotations

import re

from .calls import NameRef, ResolveContext, collect_signature_refs, dedupe_refs, plain_type_ref
from .constants import (
    DEFAULT_MAX_DOC_CHARS,
    KIND_CONST,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_TYPE,
    KIND_VAR,
)
from .goparse import ParsedGoFile, RawDecl, RawField, RawInterfaceMethod
from .model import PackageInfo, SymbolRecord, truncate_text


def symbol_id(kind: str, package_path: str, qualified_name: str) -> str:
    """Stable, greppable symbol id."""
    return f"{kind}:{package_path}:{qualified_name}"


def symbol_kind(decl: RawDecl) -> str:
    """The kind stored in the index.

    ``type`` declarations report the more precise kind (``struct``,
    ``interface``, ``alias``) so that an agent can filter on it directly.
    """
    if decl.kind == KIND_TYPE:
        return decl.type_kind or KIND_TYPE
    return decl.kind


def qualified_name_of(decl: RawDecl) -> str:
    """Name of the symbol inside its package; methods carry their receiver type."""
    if decl.kind == KIND_METHOD and decl.receiver_type:
        return f"{decl.receiver_type}.{decl.name}"
    return decl.name


def _field_line(field: RawField) -> str:
    """One-line description of a struct field."""
    if field.embedded:
        line = field.type
    else:
        line = f"{field.name} {field.type}".strip()
    if field.tag:
        line += " `" + field.tag + "`"
    if field.embedded:
        line += " [embedded]"
    return line


def member_summaries(decl: RawDecl) -> list[str]:
    """Members of a type declaration: struct fields or interface method specs.

    Embedded interfaces and constraint unions are not listed; they do not
    declare anything of this package.
    """
    if decl.fields:
        return [_field_line(field) for field in decl.fields]
    return [_iface_line(method) for method in decl.iface_methods]


def _iface_line(method: RawInterfaceMethod) -> str:
    return method.signature or method.name



_TYPE_ARG_RE = re.compile(r"\[([^\[\]]*)\]")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def receiver_type_params(receiver: str) -> frozenset[str]:
    """Type parameter names of a generic receiver, e.g. ``*List[T]`` -> ``{T}``.

    A method on a generic type does not repeat the type parameters in its own
    declaration, but they are lexical names in its body and must never be
    resolved to a project symbol.
    """
    names: set[str] = set()
    for group in _TYPE_ARG_RE.findall(receiver):
        for part in group.split(","):
            first = part.strip().split(" ")[0].strip()
            if _IDENT_RE.match(first):
                names.add(first)
    return frozenset(names)


def signature_refs(decl: RawDecl) -> tuple[NameRef, ...]:
    """Type references mentioned in a declaration's signature (not its body)."""
    fragments: list[str] = [decl.receiver, decl.results, decl.value_type]
    fragments.extend(param.type for param in decl.params)
    fragments.extend(field.type for field in decl.fields)
    fragments.extend(
        method.signature[len(method.name) :] if method.signature.startswith(method.name) else method.signature
        for method in decl.iface_methods
    )
    refs: list[NameRef] = []
    for fragment in fragments:
        refs.extend(collect_signature_refs(fragment))
    return tuple(dedupe_refs(refs))


def result_type_ref(decl: RawDecl) -> NameRef | None:
    """The plain project type ``decl`` returns, or ``None`` when there is none.

    ``*ProxyServer`` and ``memory`` name exactly one type; ``(Item, error)``,
    ``[]Item``, ``map[string]Item``, ``chan int``, ``func()`` and a named result
    such as ``(v *Item)`` do not -- the linker must not guess, so they give
    ``None``.  The arity of a multi-result declaration is kept by
    :func:`result_arity`, which is what tells ``v, err := pkg.Open()`` apart from
    ``v := New()``.
    """
    fragment = decl.results.strip()
    if fragment.startswith("(") and fragment.endswith(")"):
        fragment = fragment[1:-1].strip()
    return plain_type_ref(fragment)


def result_arity(decl: RawDecl) -> int:
    """How many values ``decl`` returns; ``0`` when it returns nothing.

    Counts the top-level comma-separated entries of ``decl.results``, so
    ``"(*ProxyServer, error)"`` is ``2`` while ``"[]int"`` and ``"map[a]b"``
    stay ``1``.  Only used to prefer the unambiguous single-result form.
    """
    fragment = decl.results.strip()
    if not fragment:
        return 0
    if fragment.startswith("(") and fragment.endswith(")"):
        fragment = fragment[1:-1].strip()
        if not fragment:
            return 0
    depth = 0
    count = 1
    for char in fragment:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count



def resolve_context(decl: RawDecl, parsed: ParsedGoFile, package_path: str) -> ResolveContext:
    """Naming environment of a declaration: imports, receiver and type parameters."""
    imports: dict[str, str] = {}
    for spec in parsed.imports:
        local = spec.local_name
        if not local or local in (".", "_"):
            continue  # dot imports are ambiguous, blank imports import nothing
        imports.setdefault(local, spec.path)
    type_params = frozenset(decl.type_params) | receiver_type_params(decl.receiver)
    return ResolveContext(
        package_path=package_path,
        imports=imports,
        receiver_var=decl.receiver_var,
        receiver_type=decl.receiver_type,
        type_params=type_params,
    )


def symbol_from_decl(
    decl: RawDecl,
    parsed: ParsedGoFile,
    package_name: str,
    package_path: str,
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
) -> SymbolRecord:
    """Build the index record for one declaration."""
    kind = symbol_kind(decl)
    qualified = qualified_name_of(decl)
    return SymbolRecord(
        id=symbol_id(kind, package_path, qualified),
        kind=kind,
        package=package_name,
        package_path=package_path,
        file=parsed.rel_path,
        name=decl.name,
        qualified_name=qualified,
        receiver=decl.receiver,
        receiver_type=decl.receiver_type,
        signature=decl.signature,
        doc=truncate_text(decl.doc, max_doc_chars),
        exported=decl.exported,
        start_line=decl.start_line,
        end_line=decl.end_line,
        start_byte=decl.start_byte,
        end_byte=decl.end_byte,
        fields=member_summaries(decl),
        imports=list(parsed.import_paths),
        test_related=decl.test_related,
        content_hash=parsed.content_hash,
    )


def symbols_for_file(
    parsed: ParsedGoFile,
    package_name: str,
    package_path: str,
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
) -> list[SymbolRecord]:
    """Build the records for every declaration of one file."""
    return [
        symbol_from_decl(decl, parsed, package_name, package_path, max_doc_chars)
        for decl in parsed.decls
    ]


def package_symbol(
    package: PackageInfo,
    *,
    doc: str = "",
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
) -> SymbolRecord:
    """Build the single record that represents a package in the index."""
    imports = sorted({path for symbol in package.symbols for path in symbol.imports})
    return SymbolRecord(
        id=symbol_id(KIND_PACKAGE, package.path, package.name),
        kind=KIND_PACKAGE,
        package=package.name,
        package_path=package.path,
        file="",
        name=package.name,
        qualified_name=package.name,
        receiver="",
        signature=f"package {package.name}",
        doc=truncate_text(doc or package.doc, max_doc_chars),
        exported=True,
        start_line=0,
        end_line=0,
        start_byte=0,
        end_byte=0,
        imports=imports,
        test_related=package.test_related,
    )


def body_span(decl: RawDecl) -> tuple[int, int] | None:
    """Byte range to scan for calls and composite literals; ``None`` if pointless.

    Functions and methods use their body.  ``const``/``var`` specs use the whole
    spec so that ``var svc = &Service{}`` is linked, and structs use their field
    body (the signature is covered by :func:`signature_refs`).

    Interface bodies are skipped on purpose: their method specs look like calls
    (``Save(...)``) but are not call sites.
    """
    if decl.body_start is not None and decl.body_end is not None:
        if decl.type_kind == KIND_INTERFACE:
            return None
        return (decl.body_start, decl.body_end)
    if decl.kind in (KIND_CONST, KIND_VAR):
        return (decl.start_byte, decl.end_byte)
    return None


def disambiguate_ids(symbols: list[SymbolRecord]) -> int:
    """Disambiguate ids shared by files with different build constraints.

    ``foo_linux.go`` and ``foo_windows.go`` declaring the same function produce
    the same id; every colliding record gets a ``#file`` suffix so that ids stay
    unique and greppable.  Returns the number of renamed records.
    """
    counts: dict[str, int] = {}
    for symbol in symbols:
        counts[symbol.id] = counts.get(symbol.id, 0) + 1
    renamed = 0
    for symbol in symbols:
        if counts[symbol.id] > 1:
            symbol.id = f"{symbol.id}#{symbol.file}"
            renamed += 1
    return renamed
