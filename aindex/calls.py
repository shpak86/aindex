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
    x.Create(...)                method on a local whose project type was inferred
    x.field.Create(...)          method on a project-typed field of such a value

The fifth case is :func:`infer_locals`: for every function body one token pass
recovers the project type of plain local names -- parameters, ``var x Type``,
``x := Constructor(...)`` (including a single-result call) and ``x := Type{}`` /
``x := &Type{}`` / ``x := new(Type)``.  Only a declaration whose type resolves to
*one* project symbol is recorded; ``x.Method()`` then resolves like any other
method.  Inference happens at indexing time, so an older ``.ai-index/`` must be
regenerated to benefit from it.

The sixth case is :func:`index_fields`, a pre-pass that pairs every struct field
with its project type, resolved in the context of the struct that declares it.
``x.field`` then counts as a value of that type, so ``x.field.Create()`` resolves
exactly like ``x.Create()`` -- which is what a service that holds its
dependencies as fields needs (``s.dialer.Dial()``).  One level deep, concrete
project types only.

Left unresolved (raw text is kept, prefixed with ``?`` in the rendered docs)::

    x.Method()                   method on a value of an unknown or external type
    iface.Method()               dynamic dispatch through an interface
    extpkg.Func()                call into a package outside the project
    someClosure()                call through a function value
    x.field.Method()             field of an interface, external, composite or
                                 unknown type, or a chain deeper than one field
    reflect.Value(...)           calls built at run time by reflection

Package-level bidirectional closure (``callers``) is computed by the pipeline
after every symbol has been resolved.
"""

from __future__ import annotations

import re
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


#: A single plain type name, optionally qualified and/or behind one pointer:
#: ``memory``, ``*memory``, ``pkg.Store``, ``*pkg.Store``.  Anything else --
#: slices, arrays, maps, channels, function types, unions, approximations and
#: generic instantiations (``T[U]``) -- names no single project type.
_PLAIN_TYPE_RE = re.compile(r"\*?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\Z")


def plain_type_ref(text: str) -> NameRef | None:
    """The :class:`NameRef` of a plain (possibly pointer) type name, else ``None``.

    ``*Service`` and ``pkg.Service`` are plain; ``[]Service``, ``map[k]v``,
    ``chan v``, ``func()``, ``A | B``, ``~int`` and ``List[T]`` are not.
    """
    fragment = text.strip()
    if not _PLAIN_TYPE_RE.match(fragment):
        return None
    refs = collect_signature_refs(fragment)
    # ``collect_signature_refs`` reports every prefix (``api`` for ``api.Server``);
    # the last ref is the whole chain, which is the type the fragment names.
    return refs[-1] if refs else None


def _ref_of(parts: list[str], line: int = 0) -> NameRef:
    """A :class:`NameRef` for an already split dotted chain (``a.b.c``)."""
    return NameRef(".".join(parts), parts[0], parts[-1], line)


def _chain_at(tokens: list[Token], index: int) -> tuple[list[str], int]:
    """Walk *forward* over a dotted chain; return its parts and last token index.

    The mirror image of :func:`_chain_tokens`; ``index`` must point at an
    identifier.  The returned index is the last token of the chain, so
    ``tokens[last + 1]`` is what follows it (``(``, ``{``, ``.``, ...).
    """
    end = index
    while (
        end + 2 < len(tokens)
        and tokens[end + 1].is_op(".")
        and tokens[end + 2].kind == TOK_IDENT
    ):
        end += 2
    return [tokens[i].text for i in range(index, end + 1, 2)], end

@dataclass(slots=True)
class SymbolIndex:
    """Lookup tables for resolving a name to exactly one project symbol id."""

    #: package path -> {name: symbol id} for package-scope symbols.
    scope: dict[str, dict[str, str]] = field(default_factory=dict)
    #: package path -> {name: symbol id} restricted to exported symbols.
    exported: dict[str, dict[str, str]] = field(default_factory=dict)
    #: (package path, base type name) -> {method name: symbol id}
    methods: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    #: (package path, base type name) -> {field name: its project type}; see
    #: :func:`index_fields`.  Only fields of one plain project type appear here.
    fields: dict[tuple[str, str], dict[str, tuple[str, str]]] = field(default_factory=dict)
    #: symbol id -> kind, used to tell types, values and callables apart.
    kinds: dict[str, str] = field(default_factory=dict)
    #: symbol id of a type -> (package path, base type name of the type).
    locations: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: symbol id of a function/method -> ((package path, type name), result count).
    results: dict[str, tuple[tuple[str, str], int]] = field(default_factory=dict)

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
        if symbol.kind in TYPE_KINDS and symbol.name:
            self.locations[symbol.id] = (symbol.package_path, symbol.name)
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

    def field(
        self, package_path: str, type_name: str, name: str
    ) -> tuple[str, str] | None:
        """The project type of field ``name`` inside ``type_name``, else ``None``."""
        return self.fields.get((package_path, base_type_name(type_name)), {}).get(name)


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
    #: inferred local name -> (package path, base type name of its project type).
    locals: dict[str, tuple[str, str]] = field(default_factory=dict)


def _value_location(name: str, ctx: ResolveContext) -> tuple[str, str] | None:
    """The project type of the *value* ``name`` denotes, when the context types it.

    The receiver of the enclosing method, or an inferred local/parameter: exactly
    the names the ``x.Method()`` rules of :func:`resolve_ref` accept as a value.
    """
    if ctx.receiver_var and name == ctx.receiver_var and ctx.receiver_type:
        return (ctx.package_path, ctx.receiver_type)
    return ctx.locals.get(name)


def resolve_ref(ref: NameRef, index: SymbolIndex, ctx: ResolveContext) -> str:
    """Resolve ``ref`` to a project symbol id; ``""`` when it cannot be resolved.

    The cases are the ones listed in the module docstring; anything else is left
    to the caller (and reported as unresolved) instead of guessing.
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
    if len(parts) == 2 and ref.base in ctx.locals:
        # ``x.Create()`` -- a method on a local of an inferred project type.
        package_path, type_name = ctx.locals[ref.base]
        return index.method(package_path, type_name, ref.name)
    if len(parts) == 3:
        return _field_call(ref, index, ctx)
    return ""


def _field_call(ref: NameRef, index: SymbolIndex, ctx: ResolveContext) -> str:
    """Resolve ``x.field.Method()``; ``""`` when the field type is not known.

    ``x`` must be a value this context can type (see :func:`_value_location`) and
    ``field`` one of its fields of a single plain project type, as recorded by
    :func:`index_fields`.  Deeper chains are left alone: ``x.a.b.Method()`` would
    need a field map per level for no real gain.
    """
    parts = ref.chain.split(".")
    owner = _value_location(parts[0], ctx)
    if owner is None:
        return ""
    field_type = index.field(owner[0], owner[1], parts[1])
    if field_type is None:
        return ""
    return index.method(field_type[0], field_type[1], ref.name)


# -- local type inference -------------------------------------------------


#: Type kinds a *value* may be attributed to.  Interfaces are deliberately
#: absent: a value of interface type dispatches dynamically, so binding its
#: calls to one symbol would be a guess (``iface.Method()`` stays unresolved).
VALUE_KINDS: frozenset[str] = frozenset({KIND_TYPE, KIND_STRUCT})


def _ref_location(
    ref: NameRef, ctx: ResolveContext, index: SymbolIndex
) -> tuple[str, str] | None:
    """``(package path, type name)`` when ``ref`` names a concrete project type."""
    target = resolve_ref(ref, index, ctx)
    if index.kind(target) in VALUE_KINDS:
        return index.locations.get(target)
    return None


def _text_location(
    text: str, ctx: ResolveContext, index: SymbolIndex
) -> tuple[str, str] | None:
    """Like :func:`_ref_location` for a raw type fragment such as ``*memory``."""
    ref = plain_type_ref(text)
    return _ref_location(ref, ctx, index) if ref is not None else None


def _shadows_existing(name: str, ctx: ResolveContext, index: SymbolIndex) -> bool:
    """True when ``name`` already means something else in this context.

    An inferred local must never *reinterpret* a name the other rules of
    :func:`resolve_ref` already handle, so a name that is a type parameter, an
    import alias, the receiver or a local type name is left alone.
    """
    if name == ctx.receiver_var or name in ctx.imports or name in ctx.type_params:
        return True
    target = index.local(ctx.package_path, name)
    return bool(target) and index.kind(target) in TYPE_KINDS


def infer_locals(
    lexed: Lexed,
    span: tuple[int, int],
    ctx: ResolveContext,
    index: SymbolIndex,
    param_types: tuple[tuple[str, str], ...],
) -> dict[str, tuple[str, str]]:
    """Infer the project type of local variables and parameters in one body.

    One token pass over ``span`` (the body byte range); the result feeds
    :attr:`ResolveContext.locals`, so ``x.Method()`` resolves whenever ``x`` has
    a single, plainly known project type -- a typed parameter, ``var x Type``,
    ``x := Constructor(...)`` (single result), ``x := Type{}`` / ``&Type{}`` or
    ``x := new(Type)``.

    Nothing is guessed: composite and external types, interface types and calls
    with more than one result are ignored, and a name inferred as two different
    types in the same body is dropped entirely.
    """
    tokens = lexed.tokens
    lo, hi = span
    found: dict[str, tuple[str, str]] = {}
    disputed: set[str] = set()

    def put(name: str, location: tuple[str, str] | None) -> None:
        if not _is_name_usable(name) or location is None or name in disputed:
            return
        if _shadows_existing(name, ctx, index):
            return
        previous = found.get(name)
        if previous is not None and previous != location:
            disputed.add(name)
            found.pop(name, None)
            return
        found[name] = location

    for name, type_text in param_types:
        put(name, _text_location(type_text, ctx, index))

    i = 0
    last = len(tokens) - 1  # the EOF token is never the start of a statement
    while i < last:
        token = tokens[i]
        if token.start < lo:
            i += 1
            continue
        if token.start >= hi:
            break
        if token.is_keyword("var"):
            _record_var(tokens, i, ctx, index, put)
        elif token.kind == TOK_IDENT and tokens[i + 1].is_op(":="):
            _record_short_var(tokens, i, ctx, index, put)
        i += 1
    return found


def _record_var(
    tokens: list[Token], i: int, ctx: ResolveContext, index: SymbolIndex, put
) -> None:
    """Handle ``var name Type`` / ``var name *Type`` starting at ``tokens[i]``."""
    j = i + 1
    if j >= len(tokens) or tokens[j].kind != TOK_IDENT:
        return  # ``var (``, ``var x, y T`` or ``var x = ...``: no plain type here
    name = tokens[j].text
    j += 1
    while j < len(tokens) and tokens[j].is_op("*"):
        j += 1
    if j >= len(tokens) or tokens[j].kind != TOK_IDENT:
        return
    parts, _ = _chain_at(tokens, j)
    if len(parts) <= 2:
        put(name, _ref_location(_ref_of(parts, tokens[j].line), ctx, index))


def _record_short_var(
    tokens: list[Token], i: int, ctx: ResolveContext, index: SymbolIndex, put
) -> None:
    """Handle ``name := rhs`` starting at ``tokens[i]`` (both tokens verified)."""
    name = tokens[i].text
    j = i + 2
    while j < len(tokens) and (tokens[j].is_op("*") or tokens[j].is_op("&")):
        j += 1
    if j >= len(tokens) or tokens[j].kind != TOK_IDENT:
        return  # ``x := []T{}``, ``x := map[K]V{}``, ``x := <-ch``, ...
    parts, end = _chain_at(tokens, j)
    if end + 1 >= len(tokens):
        return
    after = tokens[end + 1]
    if after.is_op("{"):
        if len(parts) <= 2:
            put(name, _ref_location(_ref_of(parts, tokens[j].line), ctx, index))
    elif after.is_op("("):
        if parts == ["new"]:
            _record_new(tokens, end + 1, ctx, index, put, name)
        else:
            entry = index.results.get(resolve_ref(_ref_of(parts), index, ctx))
            if entry is not None and entry[1] == 1:
                put(name, entry[0])


def _record_new(
    tokens: list[Token],
    paren: int,
    ctx: ResolveContext,
    index: SymbolIndex,
    put,
    name: str,
) -> None:
    """Handle ``name := new(Type)``; ``tokens[paren]`` is the opening ``(``."""
    inner = paren + 1
    while inner < len(tokens) and tokens[inner].is_op("*"):
        inner += 1
    if inner >= len(tokens) or tokens[inner].kind != TOK_IDENT:
        return
    parts, _ = _chain_at(tokens, inner)
    if len(parts) <= 2:
        put(name, _ref_location(_ref_of(parts, tokens[inner].line), ctx, index))



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
    #: ``(parameter name, type text)`` pairs; feed the local type inference.
    param_types: tuple[tuple[str, str], ...] = ()
    #: ``(field name, type text)`` pairs of a struct declaration; feed
    #: :func:`index_fields`.
    field_types: tuple[tuple[str, str], ...] = ()
    #: the plain type this declaration returns, when it returns exactly one.
    result_ref: NameRef | None = None
    #: how many values the declaration returns (``0`` for none).
    result_arity: int = 0


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


def index_results(links: list[SymbolLinks], index: SymbolIndex) -> None:
    """Record the single project type every callable returns in ``index.results``.

    A pre-pass, so that ``srv := api.NewServer()`` can be typed even though
    ``NewServer`` is declared in another file, whatever the declaration order.
    The return type is resolved in the callee's own context (its imports and
    receiver), never in the caller's.
    """
    for link in links:
        ref = link.result_ref
        if ref is None:
            continue
        target = resolve_ref(ref, index, link.context)
        location = index.locations.get(target) if index.kind(target) in VALUE_KINDS else None
        if location is not None:
            index.results[link.symbol.id] = (location, link.result_arity)


def index_fields(links: list[SymbolLinks], index: SymbolIndex) -> None:
    """Record the project type of every struct field in ``index.fields``.

    A pre-pass like :func:`index_results`, so that ``x.field.Method()`` can be
    typed in any file, whatever the declaration order.  The field type is resolved
    in the context of the struct that declares it (its package and its imports),
    never in the caller's.

    Only a single plain project type is recorded.  An interface field dispatches
    dynamically and an external, generic or composite one names no project
    symbol, so none of them get an entry and their calls stay unresolved.
    """
    for link in links:
        if link.symbol.kind != KIND_STRUCT:
            continue
        found: dict[str, tuple[str, str]] = {}
        for name, type_text in link.field_types:
            location = _text_location(type_text, link.context, index) if name else None
            if location is not None:
                found.setdefault(name, location)
        if found:
            key = (link.symbol.package_path, base_type_name(link.symbol.name))
            index.fields[key] = found


def link_symbols(links: list[SymbolLinks], index: SymbolIndex) -> LinkStats:
    """Resolve the references of every symbol and fill the reverse edges.

    Mutates ``symbol.calls``, ``symbol.types``, ``symbol.unresolved_calls``,
    ``symbol.methods`` (for types) and ``symbol.callers``.
    """
    index_results(links, index)
    index_fields(links, index)
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
            # Learn the project types of this body's variables first: every
            # ``x.Method()`` below then resolves like a receiver call.
            ctx.locals = infer_locals(link.lexed, link.body, ctx, index, link.param_types)
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
