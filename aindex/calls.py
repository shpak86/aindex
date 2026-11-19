"""Approximate call graph and type-reference extraction.

Go has no header files, so a static call graph without full type information is
necessarily approximate.  This module is deliberately *conservative*: a call is
either resolved to exactly one project symbol, or it is reported as unresolved
with its raw spelling.  Nothing is guessed.

Resolved::

    NewService(...)              package-scope symbol of the current package
    billing.NewService(...)      exported symbol of an imported project package
    s.Create(...)                method on the receiver of the enclosing method
    Type{...} / Type(x)          project type used as a literal or conversion

Left unresolved (raw text is kept, prefixed with ``?`` in the rendered docs)::

    x.Method()                   method on a local variable or field
    iface.Method()               dynamic dispatch through an interface
    extpkg.Func()                call into a package outside the project
    someClosure()                call through a function value

Package-level bidirectional closure (``callers``) is computed by the pipeline
after every symbol has been resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import (
    GO_BUILTIN_TYPES,
    GO_KEYWORDS,
    GO_PREDECLARED,
    KIND_CONST,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_STRUCT,
    KIND_TYPE,
    TOK_IDENT,
)
from .golex import Lexed, Token, base_type_name, lex
from .model import SymbolRecord

#: Type-declaring kinds: a conversion ``Type(x)`` resolves to one of these.
TYPE_KINDS: frozenset[str] = frozenset({KIND_TYPE, KIND_STRUCT, KIND_INTERFACE})

@dataclass(slots=True)
class NameRef:
    """A dotted name used in an expression, e.g. ``s.conn.Close`` or ``pkg.New``."""

    chain: str
    base: str
    name: str
    line: int

    def to_dict(self) -> dict[str, object]:
        return {"chain": self.chain, "base": self.base, "name": self.name, "line": self.line}


def _chain_tokens(tokens: list[Token], index: int) -> int:
    """Walk back over a dotted chain; return the index of its first token."""
    start = index
    while start - 2 >= 0 and tokens[start - 1].is_op(".") and tokens[start - 2].kind == TOK_IDENT:
        start -= 2
    return start


def _ref_at(tokens: list[Token], index: int) -> NameRef:
    start = _chain_tokens(tokens, index)
    parts = [tokens[i].text for i in range(start, index + 1, 2)]
    return NameRef(
        chain=".".join(parts),
        base=parts[0],
        name=parts[-1],
        line=tokens[index].line,
    )


def _is_name_usable(name: str) -> bool:
    """False for predeclared identifiers, keywords and builtin type names."""
    return bool(name) and name not in GO_PREDECLARED and name not in GO_KEYWORDS


# Tokens that mean the following ``{`` opens a block, not a composite literal.
_BLOCK_KEYWORDS: frozenset[str] = frozenset(
    {
        "if",
        "else",
        "for",
        "range",
        "switch",
        "select",
        "case",
        "chan",
        "map",
        "func",
        "struct",
        "interface",
    }
)
_CONDITION_OPS: frozenset[str] = frozenset(
    {"&&", "||", "==", "!=", "<", ">", "<=", ">=", "!", "++", "--", ")", "]", "}"}
)


def _opens_block(tokens: list[Token], index: int) -> bool:
    """True when ``tokens[index]`` is a block condition instead of a type name."""
    if index == 0:
        return False
    previous = tokens[index - 1]
    if previous.kind == "keyword":
        return previous.text in _BLOCK_KEYWORDS
    return previous.kind == "op" and previous.text in _CONDITION_OPS


def collect_call_refs(lexed: Lexed, lo: int, hi: int) -> list[NameRef]:
    """Collect ``ident(...)`` call sites inside the byte range ``[lo, hi)``."""
    refs: list[NameRef] = []
    tokens = lexed.tokens
    for index, token in enumerate(tokens):
        if token.start < lo:
            continue
        if token.start >= hi:
            break
        if token.kind != TOK_IDENT or not tokens[index + 1].is_op("("):
            continue
        if not _is_name_usable(token.text):
            continue
        ref = _ref_at(tokens, index)
        if ref.chain == ref.name and token.text in GO_BUILTIN_TYPES:
            continue
        refs.append(ref)
    return refs


def collect_type_refs(lexed: Lexed, lo: int, hi: int) -> list[NameRef]:
    """Collect ``Type{...}`` composite literal types inside ``[lo, hi)``."""
    refs: list[NameRef] = []
    tokens = lexed.tokens
    for index, token in enumerate(tokens):
        if token.start < lo:
            continue
        if token.start >= hi:
            break
        if token.kind != TOK_IDENT or not tokens[index + 1].is_op("{"):
            continue
        start = _chain_tokens(tokens, index)
        if not _is_name_usable(token.text) or _opens_block(tokens, start):
            continue
        refs.append(_ref_at(tokens, index))
    return refs


def collect_signature_refs(text: str) -> list[NameRef]:
    """Collect every dotted type name mentioned in a type fragment.

    Used for parameter, result, receiver and field types: ``ctx context.Context``
    yields ``context.Context``, ``[]*Service`` yields ``Service``.
    """
    fragment = text.strip()
    if not fragment:
        return []
    lexed = lex(fragment.encode("utf-8", "surrogateescape"))
    refs: list[NameRef] = []
    for index, token in enumerate(lexed.tokens):
        if token.kind != TOK_IDENT or not _is_name_usable(token.text):
            continue
        refs.append(_ref_at(lexed.tokens, index))
    return refs


def dedupe_refs(refs: list[NameRef]) -> list[NameRef]:
    """Return the refs with duplicate chains removed; first occurrence wins."""
    unique: list[NameRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.chain not in seen:
            seen.add(ref.chain)
            unique.append(ref)
    return unique

    """False for predeclared identifiers, keywords and builtin type names."""
    return bool(name) and name not in GO_PREDECLARED and name not in GO_KEYWORDS

@dataclass(slots=True)
class SymbolIndex:
    """Lookup tables for resolving a name to exactly one project symbol id."""

    #: package path -> {name: symbol id} for package-scope symbols.
    scope: dict[str, dict[str, str]] = field(default_factory=dict)
    #: package path -> {name: symbol id} restricted to exported symbols.
    exported: dict[str, dict[str, str]] = field(default_factory=dict)
    #: (package path, base type name) -> {method name: symbol id}
    methods: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    #: symbol id -> kind, used to tell types, values and callables apart.
    kinds: dict[str, str] = field(default_factory=dict)

    def add(self, symbol: SymbolRecord) -> None:
        """Register one symbol; package rows are not indexed (nothing imports them)."""
        if symbol.kind == KIND_PACKAGE:
            return
        self.kinds[symbol.id] = symbol.kind
        if symbol.kind == KIND_METHOD:
            type_name = base_type_name(symbol.receiver) or symbol.receiver_type
            if type_name:
                self.methods.setdefault((symbol.package_path, type_name), {})[symbol.name] = symbol.id
            return
        if symbol.name:
            self.scope.setdefault(symbol.package_path, {}).setdefault(symbol.name, symbol.id)
            if symbol.exported:
                self.exported.setdefault(symbol.package_path, {}).setdefault(symbol.name, symbol.id)

    def kind(self, symbol_id: str) -> str:
        return self.kinds.get(symbol_id, "")

    def local(self, package_path: str, name: str) -> str:
        return self.scope.get(package_path, {}).get(name, "")

    def imported(self, package_path: str, name: str) -> str:
        return self.exported.get(package_path, {}).get(name, "")

    def method(self, package_path: str, type_name: str, name: str) -> str:
        return self.methods.get((package_path, base_type_name(type_name)), {}).get(name, "")

    def methods_of(self, package_path: str, type_name: str) -> dict[str, str]:
        return self.methods.get((package_path, base_type_name(type_name)), {})


@dataclass(slots=True)
class ResolveContext:
    """The naming environment of one symbol, needed to resolve its references."""

    package_path: str
    #: local import name (alias or last path segment) -> import path.
    imports: dict[str, str] = field(default_factory=dict)
    #: receiver variable name of the enclosing method, e.g. ``s``.
    receiver_var: str = ""
    #: base type name of the enclosing method receiver, e.g. ``Service``.
    receiver_type: str = ""
    #: generic type parameters in scope; these are never project symbols.
    type_params: frozenset[str] = frozenset()


def resolve_ref(ref: NameRef, index: SymbolIndex, ctx: ResolveContext) -> str:
    """Resolve ``ref`` to a project symbol id; ``""`` when it cannot be resolved.

    Only the four cases listed in the module docstring are resolved; everything
    else is left to the caller (and reported as unresolved) instead of guessing.
    """
    if ref.name in ctx.type_params or ref.base in ctx.type_params:
        return ""
    if ref.chain == ref.name:
        return index.local(ctx.package_path, ref.name)
    parts = ref.chain.split(".")
    if ref.base in ctx.imports:
        path = ctx.imports[ref.base]
        if path == ctx.package_path:
            return index.local(path, ref.name)
        return index.imported(path, ref.name) if len(parts) == 2 else ""
    if len(parts) == 2 and ctx.receiver_var and ref.base == ctx.receiver_var:
        return index.method(ctx.package_path, ctx.receiver_type, ref.name)
    if len(parts) == 2 and index.kind(index.local(ctx.package_path, ref.base)) in TYPE_KINDS:
        # ``Service.Create(x)`` -- a method expression on a local type.
        return index.method(ctx.package_path, ref.base, ref.name)
    return ""



@dataclass(slots=True)
class SymbolLinks:
    """Everything needed to resolve the references of a single symbol."""

    symbol: SymbolRecord
    context: ResolveContext
    #: lexed source of the file the symbol was declared in.
    lexed: Lexed | None = None
    #: byte range of the symbol body (``{}`` block, or declaration text).
    body: tuple[int, int] | None = None
    #: name references found in the signature and in field types.
    signature_types: tuple[NameRef, ...] = ()


@dataclass(slots=True)
class LinkStats:
    """Counters collected while linking, surfaced in ``stats.json``."""

    symbols: int = 0
    calls: int = 0
    resolved_calls: int = 0
    unresolved_calls: int = 0
    type_refs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "symbols": self.symbols,
            "calls": self.calls,
            "resolved_calls": self.resolved_calls,
            "unresolved_calls": self.unresolved_calls,
            "type_refs": self.type_refs,
        }


def link_symbols(links: list[SymbolLinks], index: SymbolIndex) -> LinkStats:
    """Resolve the references of every symbol and fill the reverse edges.

    Mutates ``symbol.calls``, ``symbol.types``, ``symbol.unresolved_calls``,
    ``symbol.methods`` (for types) and ``symbol.callers``.
    """
    stats = LinkStats()
    callers: dict[str, list[str]] = {}
    for link in links:
        symbol = link.symbol
        ctx = link.context
        calls: list[str] = []
        unresolved: list[str] = []
        types: list[str] = []
        for ref in link.signature_types:
            target = resolve_ref(ref, index, ctx)
            if target and index.kind(target) in TYPE_KINDS:
                types.append(target)
        if link.lexed is not None and link.body is not None:
            lo, hi = link.body
            for ref in dedupe_refs(collect_type_refs(link.lexed, lo, hi)):
                target = resolve_ref(ref, index, ctx)
                if target and index.kind(target) in TYPE_KINDS:
                    types.append(target)
            for ref in dedupe_refs(collect_call_refs(link.lexed, lo, hi)):
                target = resolve_ref(ref, index, ctx)
                kind = index.kind(target)
                if not target:
                    unresolved.append(ref.chain)
                elif kind in TYPE_KINDS:
                    types.append(target)  # conversion such as ``Service(x)``
                elif kind == KIND_CONST:
                    unresolved.append(ref.chain)  # a constant is not callable
                else:
                    calls.append(target)  # function, method or func-typed var
        symbol.calls = sorted(set(calls))
        symbol.unresolved_calls = sorted(set(unresolved))
        symbol.types = sorted(set(types))
        if symbol.kind in TYPE_KINDS:
            symbol.methods = sorted(index.methods_of(symbol.package_path, symbol.name).values())
        for target in symbol.calls:
            callers.setdefault(target, []).append(symbol.id)
        stats.symbols += 1
        stats.calls += len(symbol.calls)
        stats.resolved_calls += len(symbol.calls)
        stats.unresolved_calls += len(symbol.unresolved_calls)
        stats.type_refs += len(symbol.types)
    for link in links:
        link.symbol.callers = sorted(set(callers.get(link.symbol.id, [])))
    return stats
