"""Go declaration parsing and symbol extraction (pure Python backend).

The parser walks the token stream produced by :mod:`aindex.golex` at brace depth
zero.  It is *not* a full Go grammar and deliberately does not try to reproduce
the Go type checker; it extracts exactly what an AI agent needs to navigate a
code base:

* package clause and package doc
* imports (path + alias)
* ``const`` / ``var`` declarations (including grouped blocks)
* ``type`` declarations (struct / interface / alias / defined type), with generics
* struct fields (names, types, tags, embedded fields)
* interface method specs
* functions and methods, with receiver, type parameters and full signatures
* doc comments attached the way godoc attaches them
* exact byte offsets and line ranges for every declaration
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .constants import (
    BLANK_IDENTIFIER,
    GENERATED_CODE_MARKERS,
    KIND_ALIAS,
    KIND_CONST,
    KIND_FUNC,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_STRUCT,
    KIND_TEST,
    KIND_TYPE,
    KIND_VAR,
    TEST_FILE_SUFFIX,
    TEST_FUNC_PREFIXES,
    TOK_IDENT,
    TOK_KEYWORD,
    TOK_RAWSTRING,
    TOK_SEMI,
    TOK_STRING,
)
from .golex import Lexed, Token, base_type_name, lex
from .model import ImportSpec, content_hash


@dataclass(slots=True)
class RawParam:
    """A function parameter or result name/type pair."""

    name: str
    type: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type}


@dataclass(slots=True)
class RawField:
    """A struct field (or embedded field)."""

    name: str
    type: str
    embedded: bool
    doc: str = ""
    tag: str = ""
    exported: bool = False
    start_byte: int = 0
    end_byte: int = 0
    start_line: int = 0
    end_line: int = 0


@dataclass(slots=True)
class RawInterfaceMethod:
    """A method spec inside an interface type."""

    name: str
    signature: str
    doc: str = ""
    exported: bool = False
    start_byte: int = 0
    end_byte: int = 0
    start_line: int = 0
    end_line: int = 0


@dataclass(slots=True)
class RawDecl:
    """A top-level declaration, ready to be turned into :class:`SymbolRecord`s."""

    kind: str
    name: str
    signature: str
    doc: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    #: End of the signature part (start of the body, when there is one).
    decl_end: int = 0
    body_start: int | None = None
    body_end: int | None = None
    receiver: str = ""
    receiver_var: str = ""
    receiver_type: str = ""
    type_kind: str = ""
    value_type: str = ""
    type_params: tuple[str, ...] = ()
    params: tuple[RawParam, ...] = ()
    results: str = ""
    fields: tuple[RawField, ...] = ()
    iface_methods: tuple[RawInterfaceMethod, ...] = ()
    exported: bool = False
    test_related: bool = False


@dataclass(slots=True)
class ParsedGoFile:
    """Result of parsing one Go file."""

    rel_path: str
    lexed: Lexed
    content_hash: str
    lines: int
    package_name: str = ""
    package_doc: str = ""
    package_span: tuple[int, int, int, int] = (0, 0, 0, 0)
    imports: list[ImportSpec] = field(default_factory=list)
    build_tags: list[str] = field(default_factory=list)
    generated: bool = False
    decls: list[RawDecl] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def import_paths(self) -> list[str]:
        return [spec.path for spec in self.imports]


def parse_source(raw: bytes, rel_path: str) -> ParsedGoFile:
    """Parse Go source bytes into a :class:`ParsedGoFile`.  Never raises."""
    lexed = lex(raw)
    parsed = ParsedGoFile(
        rel_path=rel_path,
        lexed=lexed,
        content_hash=content_hash(raw),
        lines=max(lexed.line_count, raw.count(b"\n") + 1),
        errors=list(lexed.errors),
    )
    parsed.generated = _looks_generated(lexed)
    _DeclParser(parsed).run()
    return parsed


def _looks_generated(lexed: Lexed) -> bool:
    """Detect the standard ``Code generated ... DO NOT EDIT.`` marker."""
    head = lexed.src[:4096]
    return all(marker in head for marker in GENERATED_CODE_MARKERS)


def _is_exported(name: str) -> bool:
    """Go export rule: first character is an upper case letter."""
    return bool(name) and name[0].isupper()


class _TokenStream(list):
    """Token list that yields the trailing EOF token for out-of-range indexes.

    Truncated (half-written, unsaved) files are common while an agent edits a
    project, and every parser path indexes a few tokens ahead.  Returning the
    final EOF token instead of raising keeps ``parse_source`` total: it always
    produces an index, with errors reported in ``ParsedGoFile.errors``.
    """

    __slots__ = ()

    def __getitem__(self, index: int | slice):
        if isinstance(index, slice):
            return list.__getitem__(self, index)
        if -len(self) <= index < len(self):
            return list.__getitem__(self, index)
        return list.__getitem__(self, -1)




_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")": "(", "]": "[", "}": "{"}


def _split_top_level(tokens: list[Token], separator: str) -> list[list[Token]]:
    """Split ``tokens`` on ``separator`` ignoring separators inside brackets."""
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in tokens:
        if token.kind == "op":
            if token.text in _OPENERS:
                depth += 1
            elif token.text in _CLOSERS:
                depth -= 1
            elif token.text == separator and depth == 0:
                parts.append([])
                continue
        parts[-1].append(token)
    return [part for part in parts if part]


def _unquote(literal: str) -> str:
    """Best-effort unquoting of a Go string literal (import paths, tags)."""
    if len(literal) >= 2 and literal[0] == literal[-1] and literal[0] in "\"`":
        inner = literal[1:-1]
        if literal[0] == '"':
            return inner.replace("\\\\", "\\").replace('\\"', '"')
        return inner
    return literal


def _type_param_names(tokens: list[Token]) -> list[str]:
    """Names declared in a type parameter list, e.g. ``[K comparable, V any]``."""
    names: list[str] = []
    for part in _split_top_level(tokens, ","):
        if part and part[0].kind == TOK_IDENT:
            names.append(part[0].text)
    return names


def _parse_param_list(tokens: list[Token], text_of: Callable[[list[Token]], str]) -> list[RawParam]:
    """Parse a parameter/result list into name/type pairs.

    Handles grouped names (``a, b int``), unnamed parameters (``int``), variadics
    (``args ...string``) and qualified types (``ctx context.Context``).  A list is
    considered *named* as soon as one element spells ``name type``, which mirrors
    the rule used by ``go/parser`` (a list never mixes named and unnamed entries).
    """
    entries: list[tuple[str, str, str]] = []
    named = False
    for part in _split_top_level(tokens, ","):
        raw = text_of(part)
        if _is_name_and_type(part):
            named = True
            entries.append((part[0].text, text_of(part[1:]), raw))
        elif len(part) == 1 and part[0].kind == TOK_IDENT:
            entries.append((part[0].text, "", raw))
        else:
            entries.append(("", raw, raw))
    if not named:
        return [RawParam(name="", type=raw) for _, _, raw in entries]
    # `a, b int` -- the trailing type applies to every preceding bare name.
    resolved: list[RawParam] = []
    for index, (name, type_text, _raw) in enumerate(entries):
        if not type_text and name:
            type_text = next(
                (entry[1] for entry in entries[index + 1 :] if entry[1]), ""
            )
        resolved.append(RawParam(name=name, type=type_text))
    return resolved


def _is_name_and_type(part: list[Token]) -> bool:
    """True when a comma separated element looks like ``name type``."""
    return (
        len(part) > 1
        and part[0].kind == TOK_IDENT
        and not part[1].is_op(".")
    )



def _is_generic_embedding(tokens: list[Token]) -> bool:
    """True for embedded generic fields such as ``Foo[T]`` (vs. ``Foo [3]int``)."""
    return (
        len(tokens) == 4
        and tokens[1].is_op("[")
        and tokens[2].kind == TOK_IDENT
        and tokens[3].is_op("]")
    )


def _split_field(
    tokens: list[Token], text_of: Callable[[list[Token]], str]
) -> tuple[list[str], str, bool]:
    """Split struct field tokens into ``(names, type_text, embedded)``."""
    first = tokens[0]
    if first.kind != TOK_IDENT or len(tokens) == 1 or tokens[1].is_op("."):
        type_text = text_of(tokens)
        return [base_type_name(type_text)], type_text, True
    if _is_generic_embedding(tokens):
        type_text = text_of(tokens)
        return [base_type_name(type_text)], type_text, True
    names: list[str] = []
    index = 0
    while index + 1 < len(tokens) and tokens[index].kind == TOK_IDENT and tokens[index + 1].is_op(","):
        names.append(tokens[index].text)
        index += 2
    if index < len(tokens) and tokens[index].kind == TOK_IDENT:
        names.append(tokens[index].text)
        index += 1
    if not names:
        type_text = text_of(tokens)
        return [base_type_name(type_text)], type_text, True
    return names, text_of(tokens[index:]), False


_BUILD_TAG_RE = re.compile(r"^\s*//\s*(go:build|\+build)\s+(.+?)\s*$", re.MULTILINE)


def _extract_build_tags(head: str) -> list[str]:
    """Build constraints from the file head (``//go:build`` and legacy ``// +build``)."""
    return [f"{match.group(1).strip()} {match.group(2).strip()}" for match in _BUILD_TAG_RE.finditer(head)]




class _DeclParser:
    """Depth-zero declaration scanner over a Go token stream."""

    def __init__(self, parsed: ParsedGoFile) -> None:
        self.parsed = parsed
        self.lexed = parsed.lexed
        # Out-of-range look-ahead resolves to the final EOF token (see _TokenStream).
        self.tokens = _TokenStream(self.lexed.tokens)
        self.pos = 0

    # -- cursor -----------------------------------------------------------

    def tok(self, offset: int = 0) -> Token:
        index = self.pos + offset
        if index >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[index]

    def at(
        self,
        offset: int = 0,
        *,
        op: str | None = None,
        keyword: str | None = None,
        kind: str | None = None,
    ) -> bool:
        token = self.tok(offset)
        if op is not None and not token.is_op(op):
            return False
        if keyword is not None and not token.is_keyword(keyword):
            return False
        if kind is not None and token.kind != kind:
            return False
        return True

    def match(self, index: int) -> int:
        """Index of the bracket that closes ``tokens[index]``."""
        stack: list[str] = []
        i = index
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "op":
                if token.text in _OPENERS:
                    stack.append(token.text)
                elif token.text in _CLOSERS:
                    if not stack:
                        return i
                    stack.pop()
                    if not stack:
                        return i
            i += 1
        return len(self.tokens) - 1

    def spec_end(self, index: int) -> int:
        """Index of the token that terminates the spec starting at ``index``."""
        depth = 0
        i = index
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "eof":
                return i
            if token.kind == TOK_SEMI and depth == 0:
                return i
            if token.kind == "op":
                if token.text in _OPENERS:
                    depth += 1
                elif token.text in _CLOSERS:
                    if depth == 0:
                        return i
                    depth -= 1
            i += 1
        return i

    def inline_text(self, tokens: list[Token]) -> str:
        if not tokens:
            return ""
        return self.lexed.text_without_comments(tokens[0].start, tokens[-1].end)

    def error(self, message: str, token: Token) -> None:
        self.parsed.errors.append(f"{self.parsed.rel_path}:{token.line}: {message}")

    # -- top level --------------------------------------------------------

    def run(self) -> None:
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]
            if token.kind == "eof":
                break
            if token.kind == TOK_SEMI:
                self.pos += 1
                continue
            before = self.pos
            if token.is_keyword("package") and not self.parsed.package_name:
                self.parse_package()
            elif token.is_keyword("package"):
                self.error("duplicate package clause", token)
                self.pos = self.spec_end(self.pos + 1)
            elif token.is_keyword("import"):
                self.parse_imports()
            elif token.is_keyword("const", "var"):
                self.parse_value_decl(token.text)
            elif token.is_keyword("type"):
                self.parse_type_decl()
            elif token.is_keyword("func"):
                self.parse_func_decl()
            else:
                self.error(f"unexpected {token.text!r} at top level", token)
                self.pos = self.spec_end(self.pos + 1)
            if self.pos <= before:
                # Defensive: never spin on a malformed declaration.
                self.pos = before + 1

    # -- package / imports ------------------------------------------------

    def parse_package(self) -> None:
        clause = self.tok()
        name_token = self.tok(1)
        if name_token.kind != TOK_IDENT:
            self.error("package clause without name", clause)
            self.pos = self.spec_end(self.pos + 1)
            return
        self.parsed.package_name = name_token.text
        self.parsed.package_span = (clause.start, name_token.end, clause.line, name_token.line)
        doc, _ = self.lexed.doc_before(clause.start)
        self.parsed.package_doc = doc
        self.parsed.build_tags = _extract_build_tags(self.lexed.src[: clause.start])
        self.pos = self.spec_end(self.pos + 1)

    def parse_imports(self) -> None:
        if self.at(1, op="("):
            close = self.match(self.pos + 1)
            self.parse_import_specs(self.pos + 2, close)
            self.pos = close + 1
        else:
            end = self.spec_end(self.pos + 1)
            self.parse_import_specs(self.pos + 1, end)
            self.pos = end

    def parse_import_specs(self, lo: int, hi: int) -> None:
        """Parse ``import`` specs; ``hi`` bounds the group (exclusive)."""
        i = lo
        while i < hi:
            token = self.tokens[i]
            if token.kind == TOK_SEMI:
                i += 1
                continue
            if token.kind == TOK_STRING:
                self.parsed.imports.append(ImportSpec(path=_unquote(token.text), line=token.line))
                i += 1
                continue
            if (token.kind == TOK_IDENT or token.is_op(".")) and i + 1 < hi:
                target = self.tokens[i + 1]
                if target.kind == TOK_STRING:
                    self.parsed.imports.append(
                        ImportSpec(path=_unquote(target.text), alias=token.text, line=target.line)
                    )
                    i += 2
                    continue
            self.error(f"unexpected token {token.text!r} in import block", token)
            next_i = self.spec_end(i)
            i = next_i + 1 if next_i <= i else next_i

    # -- const / var ------------------------------------------------------

    def parse_value_decl(self, keyword: str) -> None:
        group_doc, _ = self.lexed.doc_before(self.tok().start)
        if self.at(1, op="("):
            close = self.match(self.pos + 1)
            self.parse_value_specs(keyword, self.pos + 2, close, group_doc)
            self.pos = close + 1
        else:
            end = self.spec_end(self.pos + 1)
            # Same as ``type``: the comment is attached to the keyword, not the name.
            self.parse_value_specs(keyword, self.pos + 1, end, group_doc)
            self.pos = end

    def parse_value_specs(self, keyword: str, lo: int, hi: int, group_doc: str) -> None:
        carried = ""
        i = lo
        while i < hi:
            token = self.tokens[i]
            if token.kind == TOK_SEMI:
                i += 1
                continue
            end = min(self.spec_end(i), hi)
            if end <= i:
                end = min(i + 1, hi)
            carried = self.value_spec(keyword, self.tokens[i:end], group_doc, carried)
            i = end

    def value_spec(self, keyword: str, spec: list[Token], group_doc: str, carried: str = "") -> str:
        """Turn one ``const``/``var`` spec into one declaration per name.

        Returns the explicit type of the spec, which ``const`` blocks carry over to
        the following specs (``const (C int = iota * 2; D)`` makes ``D`` an ``int``).
        """
        if not spec:
            return carried
        doc, _ = self.lexed.doc_before(spec[0].start)
        doc = doc or group_doc
        equals = next((i for i, t in enumerate(spec) if t.is_op("=")), -1)
        lhs = spec[:equals] if equals >= 0 else spec
        names: list[tuple[str, str]] = []
        for part in _split_top_level(lhs, ","):
            first = part[0]
            if first.kind != TOK_IDENT:
                self.error(f"unsupported {keyword} declaration", first)
                return carried
            names.append((first.text, self.inline_text(part[1:]) if len(part) > 1 else ""))
        explicit = next((type_text for _, type_text in names if type_text), "")
        fallback = explicit or (carried if keyword == "const" else "")
        text = self.inline_text(spec)
        if text.startswith(keyword):
            text = text[len(keyword) :].strip()
        for name, type_text in names:
            if name == BLANK_IDENTIFIER:
                # ``var _ Store = (*memory)(nil)`` declares nothing to navigate to.
                continue
            self.parsed.decls.append(
                RawDecl(
                    kind=KIND_CONST if keyword == "const" else KIND_VAR,
                    name=name,
                    signature=f"{keyword} {text}".strip(),
                    doc=doc,
                    start_byte=spec[0].start,
                    end_byte=spec[-1].end,
                    start_line=spec[0].line,
                    end_line=spec[-1].line,
                    decl_end=spec[-1].end,
                    exported=_is_exported(name),
                    test_related=self._is_test_file(),
                    value_type=type_text or fallback,
                )
            )
        return fallback

    def _is_test_file(self) -> bool:
        return self.parsed.rel_path.endswith(TEST_FILE_SUFFIX)


    # -- type declarations ------------------------------------------------

    def parse_type_decl(self) -> None:
        group_doc, _ = self.lexed.doc_before(self.tok().start)
        if self.at(1, op="("):
            close = self.match(self.pos + 1)
            self.parse_type_specs(self.pos + 2, close, group_doc)
            self.pos = close + 1
        else:
            end = self.spec_end(self.pos + 1)
            # The doc comment sits above the ``type`` keyword, not above the name,
            # so it has to be carried over to the (single) spec.
            self.parse_type_specs(self.pos + 1, end, group_doc)
            self.pos = end

    def parse_type_specs(self, lo: int, hi: int, group_doc: str) -> None:
        i = lo
        while i < hi:
            token = self.tokens[i]
            if token.kind == TOK_SEMI:
                i += 1
                continue
            end = min(self.spec_end(i), hi)
            if end <= i:
                end = min(i + 1, hi)
            self.type_spec(i, end, group_doc)
            i = end

    def type_spec(self, lo: int, hi: int, group_doc: str) -> None:
        """Parse ``Name [TypeParams] (=)? Type`` inside a ``type`` declaration."""
        first = self.tokens[lo]
        if first.kind != TOK_IDENT:
            self.error(f"unsupported type declaration {first.text!r}", first)
            return
        doc, _ = self.lexed.doc_before(first.start)
        doc = doc or group_doc
        i = lo + 1
        type_params: tuple[str, ...] = ()
        if i < hi and self.tokens[i].is_op("["):
            close = self.match(i)
            type_params = tuple(_type_param_names(self.tokens[i + 1 : close]))
            i = close + 1
        is_alias = i < hi and self.tokens[i].is_op("=")
        if is_alias:
            i += 1
        fields: tuple[RawField, ...] = ()
        iface_methods: tuple[RawInterfaceMethod, ...] = ()
        body_start: int | None = None
        body_end: int | None = None
        type_kind = KIND_ALIAS if is_alias else KIND_TYPE
        end_index = hi - 1
        sig_end = self.tokens[hi - 1].end
        has_body = i + 1 < hi and self.tokens[i + 1].is_op("{")
        if has_body and self.tokens[i].is_keyword("struct"):
            close = self.match(i + 1)
            type_kind = KIND_STRUCT
            fields = tuple(self.parse_struct_body(i + 2, close))
            body_start, body_end = self.tokens[i + 1].start, self.tokens[close].end
            sig_end, end_index = body_start, close
        elif has_body and self.tokens[i].is_keyword("interface"):
            close = self.match(i + 1)
            type_kind = KIND_INTERFACE
            iface_methods = tuple(self.parse_interface_body(i + 2, close))
            body_start, body_end = self.tokens[i + 1].start, self.tokens[close].end
            sig_end, end_index = body_start, close
        signature = "type " + self.lexed.text_without_comments(first.start, sig_end).strip()
        self.parsed.decls.append(
            RawDecl(
                kind=KIND_TYPE,
                name=first.text,
                signature=signature,
                doc=doc,
                start_byte=first.start,
                end_byte=self.tokens[end_index].end,
                start_line=first.line,
                end_line=self.tokens[end_index].line,
                decl_end=sig_end,
                body_start=body_start,
                body_end=body_end,
                type_kind=type_kind,
                type_params=type_params,
                fields=fields,
                iface_methods=iface_methods,
                exported=_is_exported(first.text),
                test_related=self._is_test_file(),
            )
        )

    # -- struct / interface bodies ---------------------------------------

    def parse_struct_body(self, lo: int, hi: int) -> list[RawField]:
        """Parse the inside of a ``struct { ... }`` block into fields."""
        fields: list[RawField] = []
        i = lo
        while i < hi:
            if self.tokens[i].kind == TOK_SEMI:
                i += 1
                continue
            end = min(self.spec_end(i), hi)
            if end <= i:
                end = min(i + 1, hi)
            fields.extend(self.struct_field(self.tokens[i:end]))
            i = end
        return fields

    def struct_field(self, spec: list[Token]) -> list[RawField]:
        """Parse a single struct field spec (possibly declaring several names)."""
        if not spec:
            return []
        doc, _ = self.lexed.doc_before(spec[0].start)
        tokens = list(spec)
        tag = ""
        if tokens[-1].kind in (TOK_STRING, TOK_RAWSTRING):
            tag = _unquote(tokens[-1].text)
            tokens = tokens[:-1]
        if not tokens:
            return []
        names, type_text, embedded = _split_field(tokens, self.inline_text)
        if not type_text:
            return []
        return [
            RawField(
                name=name,
                type=type_text,
                embedded=embedded,
                doc=doc,
                tag=tag,
                exported=_is_exported(name),
                start_byte=spec[0].start,
                end_byte=spec[-1].end,
                start_line=spec[0].line,
                end_line=spec[-1].line,
            )
            for name in names
        ]

    def parse_interface_body(self, lo: int, hi: int) -> list[RawInterfaceMethod]:
        """Parse method specs from an ``interface { ... }`` block.

        Embedded interfaces and type sets (``~int | string``) are skipped: they do
        not declare a symbol of this package.
        """
        methods: list[RawInterfaceMethod] = []
        i = lo
        while i < hi:
            if self.tokens[i].kind == TOK_SEMI:
                i += 1
                continue
            end = min(self.spec_end(i), hi)
            if end <= i:
                end = min(i + 1, hi)
            spec = self.tokens[i:end]
            if len(spec) > 1 and spec[0].kind == TOK_IDENT and spec[1].is_op("("):
                doc, _ = self.lexed.doc_before(spec[0].start)
                methods.append(
                    RawInterfaceMethod(
                        name=spec[0].text,
                        signature=self.inline_text(spec),
                        doc=doc,
                        exported=_is_exported(spec[0].text),
                        start_byte=spec[0].start,
                        end_byte=spec[-1].end,
                        start_line=spec[0].line,
                        end_line=spec[-1].line,
                    )
                )
            i = end
        return methods

    # -- functions and methods -------------------------------------------

    def parse_func_decl(self) -> None:
        func_token = self.tok()
        doc, _ = self.lexed.doc_before(func_token.start)
        i = self.pos + 1
        receiver = receiver_var = receiver_type = ""
        if self.tokens[i].is_op("("):
            close = self.match(i)
            receiver, receiver_var, receiver_type = self.parse_receiver(self.tokens[i + 1 : close])
            i = close + 1
        if self.tokens[i].kind != TOK_IDENT:
            self.error("function declaration without name", func_token)
            self.pos = max(self.spec_end(i), i)
            return
        name_token = self.tokens[i]
        i += 1
        type_params: tuple[str, ...] = ()
        if self.tokens[i].is_op("["):
            close = self.match(i)
            type_params = tuple(_type_param_names(self.tokens[i + 1 : close]))
            i = close + 1
        params: tuple[RawParam, ...] = ()
        if self.tokens[i].is_op("("):
            close = self.match(i)
            params = tuple(_parse_param_list(self.tokens[i + 1 : close], self.inline_text))
            i = close + 1
        else:
            self.error(f"function {name_token.text} has no parameter list", name_token)
        results = ""
        if self.tokens[i].is_op("("):
            close = self.match(i)
            results = self.inline_text(self.tokens[i : close + 1])
            i = close + 1
        elif self.starts_type(i):
            end = self.consume_type(i)
            results = self.inline_text(self.tokens[i:end])
            i = end
        body_start: int | None = None
        body_end: int | None = None
        if self.tokens[i].is_op("{"):
            close = self.match(i)
            decl_end = self.tokens[i].start
            body_start, body_end = self.tokens[i].start, self.tokens[close].end
            end_index = close
            self.pos = close + 1
        else:
            stop = self.spec_end(i)
            decl_end = self.tokens[max(i - 1, self.pos)].end
            end_index = max(stop - 1, i - 1, self.pos)
            self.pos = stop
        last = self.tokens[end_index]
        signature = self.lexed.text_without_comments(func_token.start, decl_end).strip()
        test_related = self._is_test_file() and name_token.text.startswith(TEST_FUNC_PREFIXES)
        if receiver:
            kind = KIND_METHOD
        elif test_related:
            kind = KIND_TEST
        else:
            kind = KIND_FUNC
        self.parsed.decls.append(
            RawDecl(
                kind=kind,
                name=name_token.text,
                signature=signature,
                doc=doc,
                start_byte=func_token.start,
                end_byte=last.end,
                start_line=func_token.line,
                end_line=last.line,
                decl_end=decl_end,
                body_start=body_start,
                body_end=body_end,
                receiver=receiver,
                receiver_var=receiver_var,
                receiver_type=receiver_type,
                type_params=type_params,
                params=params,
                results=results,
                exported=_is_exported(name_token.text),
                test_related=test_related,
            )
        )

    def parse_receiver(self, tokens: list[Token]) -> tuple[str, str, str]:
        """Return ``(receiver_type_text, receiver_var, base_type_name)``."""
        tokens = [token for token in tokens if token.kind != TOK_SEMI]
        if not tokens:
            return "", "", ""
        var_name = ""
        rest = tokens
        if len(tokens) > 1 and tokens[0].kind == TOK_IDENT:
            var_name = tokens[0].text
            rest = tokens[1:]
        text = self.inline_text(rest)
        return text, var_name, base_type_name(text)

    def starts_type(self, index: int) -> bool:
        """True when ``tokens[index]`` can begin a type expression."""
        token = self.tokens[index]
        if token.kind == TOK_IDENT or token.kind == TOK_STRING:
            return True
        if token.kind == TOK_KEYWORD:
            return token.text in ("map", "chan", "func", "interface", "struct")
        return token.is_op("*", "[", "<-", "...", "(", "~")

    def consume_type(self, index: int) -> int:
        """Index just past the type expression starting at ``index``."""
        tokens = self.tokens
        token = tokens[index]
        if token.is_op("*", "<-", "..."):
            return self.consume_type(min(index + 1, len(tokens) - 1))
        if token.is_op("["):
            # ``[]T``, ``[4]byte`` and ``[...]byte`` are all ``[size]element``.
            return self.consume_type(self.match(index) + 1)
        if token.is_keyword("map") and tokens[index + 1].is_op("["):
            return self.consume_type(self.match(index + 1) + 1)
        if token.is_keyword("chan"):
            start = index + 1 + (1 if tokens[index + 1].is_op("<-") else 0)
            return self.consume_type(start)
        if token.is_keyword("func"):
            j = index + 1
            if tokens[j].is_op("("):
                j = self.match(j) + 1
            return self.consume_type(j)
        if token.is_keyword("interface", "struct") and tokens[index + 1].is_op("{"):
            return self.match(index + 1) + 1
        if token.kind == TOK_IDENT:
            j = index + 1
            while tokens[j].is_op("."):
                j += 2
            if tokens[j].is_op("["):
                j = self.match(j) + 1
            return j
        return index + 1

