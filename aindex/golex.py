"""A small, dependency-free lexer for Go source code.

The lexer is deliberately *not* a full Go parser: its job is to produce a token
stream with **exact byte offsets** so that declarations, godoc comments and line
ranges can be sliced straight out of the original source.

Design note: the source is kept as a ``latin-1`` view of the raw bytes
(``raw.decode("latin-1")``).  In that view one character is exactly one byte, so
character offsets equal byte offsets and no re-encoding is needed while scanning.
Slices that end up in the index are decoded back to real UTF-8 text.

The lexer implements Go's automatic semicolon insertion rules, which is what
makes it possible to parse ``const ( ... )`` blocks, ``import ( ... )`` blocks and
single-line ``var x = 1`` declarations without a real grammar.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .constants import (
    GO_KEYWORDS,
    SEMICOLON_TRIGGER_KEYWORDS,
    SEMICOLON_TRIGGER_KINDS,
    SEMICOLON_TRIGGER_TOKENS,
    TOK_CHAR,
    TOK_EOF,
    TOK_IDENT,
    TOK_KEYWORD,
    TOK_NUMBER,
    TOK_OP,
    TOK_RAWSTRING,
    TOK_SEMI,
    TOK_STRING,
)

_WHITESPACE = " \t\r\f\v"
_IDENT_START_RE = re.compile(r"[A-Za-z_]")


@dataclass(slots=True, frozen=True)
class Token:
    """A single Go token.

    ``start``/``end`` are byte offsets into the original file (``end`` is
    exclusive).  ``line`` is the 1-based line number of ``start``.
    """

    kind: str
    text: str
    start: int
    end: int
    line: int

    def is_op(self, *texts: str) -> bool:
        return self.kind == TOK_OP and self.text in texts

    def is_keyword(self, *texts: str) -> bool:
        return self.kind == TOK_KEYWORD and self.text in texts


@dataclass(slots=True, frozen=True)
class Comment:
    """A ``//`` or ``/* */`` comment.  ``block`` is True for ``/* */``."""

    start: int
    end: int
    block: bool
    line: int


@dataclass(slots=True)
class Lexed:
    """Lexer result: token stream plus helpers for slicing the source."""

    raw: bytes
    src: str
    tokens: list[Token] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    line_starts: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # -- offsets ----------------------------------------------------------

    def line_of(self, pos: int) -> int:
        """Return the 1-based line number containing ``pos``."""
        return bisect_right(self.line_starts, pos)

    @property
    def line_count(self) -> int:
        return len(self.line_starts)

    def text(self, start: int, end: int) -> str:
        """Decode the byte range ``[start, end)`` back to UTF-8 text."""
        if end <= start:
            return ""
        return self.raw[start:end].decode("utf-8", errors="replace")

    def text_without_comments(self, start: int, end: int) -> str:
        """Return ``[start, end)`` with comments blanked out and whitespace collapsed."""
        chars = list(self.src[start:end])
        for comment in self.comments:
            if comment.end <= start:
                continue
            if comment.start >= end:
                break
            lo = max(comment.start, start) - start
            hi = min(comment.end, end) - start
            for i in range(lo, hi):
                chars[i] = " "
        collapsed = re.sub(r"\s+", " ", "".join(chars)).strip()
        return collapsed.encode("latin-1", errors="replace").decode("utf-8", errors="replace")

    # -- comments ---------------------------------------------------------

    def doc_before(self, pos: int) -> tuple[str, int | None]:
        """Return the doc comment attached to a declaration starting at ``pos``.

        Mirrors godoc behaviour: the comment group must sit immediately above the
        declaration (no blank line in between).  Returns ``(doc_text,
        comment_start_byte)``; ``("", None)`` when nothing is attached.
        """
        group_start = self._comment_group_start_before(pos)
        if group_start is None:
            return "", None
        return self._render_comment_group(group_start, pos), group_start

    def _comment_group_start_before(self, pos: int) -> int | None:
        index = bisect_right([c.end for c in self.comments], pos)
        if index == 0:
            return None
        last = self.comments[index - 1]
        if not self._is_attached_gap(last.end, pos):
            return None
        group_start = index - 1
        while group_start > 0:
            previous = self.comments[group_start - 1]
            if previous.end > last.start or not self._is_attached_gap(previous.end, last.start):
                break
            last = previous
            group_start -= 1
        return self.comments[group_start].start

    def doc_for_range(self, start: int, end: int) -> str:
        """Return the doc comment attached to the item spanning ``[start, end)``."""
        index = bisect_right([c.start for c in self.comments], start)
        while index < len(self.comments):
            comment = self.comments[index]
            if comment.end > end:
                break
            if self._is_attached_gap(comment.end, end):
                return self._render_comment_group(comment.start, end)
            index += 1
        return ""

    def _is_attached_gap(self, gap_start: int, gap_end: int) -> bool:
        """True when ``src[gap_start:gap_end]`` is whitespace spanning <= 1 newline."""
        if gap_end <= gap_start:
            return True
        gap = self.src[gap_start:gap_end]
        return gap.count("\n") <= 1 and gap.strip() == ""

    def _render_comment_group(self, group_start: int, pos: int) -> str:
        pieces: list[str] = []
        for comment in self.comments:
            if comment.end <= group_start or comment.start >= pos:
                continue
            raw_text = self.text(comment.start, comment.end)
            pieces.append(
                _strip_block_comment(raw_text) if comment.block else _strip_line_comment(raw_text)
            )
        return "\n".join(pieces).strip()



def _strip_line_comment(text: str) -> str:
    body = text[2:] if text.startswith("//") else text
    return body[1:] if body.startswith(" ") else body


def _strip_block_comment(text: str) -> str:
    body = text[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    indent = min(indents) if indents else 0
    return "\n".join(line[indent:] if line.strip() else "" for line in lines)


def lex(raw: bytes) -> Lexed:
    """Tokenize Go source bytes.

    Never raises: syntax problems are reported through ``Lexed.errors`` so that a
    single broken file cannot abort an index run.
    """
    lexed = Lexed(raw=raw, src=raw.decode("latin-1"), line_starts=[0])
    _Scanner(lexed).run()
    return lexed



class _Scanner:
    """Single-pass Go tokenizer.

    Works on ``Lexed.src`` (the latin-1 view) so every offset is a byte offset.
    """

    def __init__(self, lexed: Lexed) -> None:
        self.lexed = lexed
        self.src = lexed.src
        self.n = len(lexed.src)

    # -- helpers ----------------------------------------------------------

    def _line(self) -> int:
        return len(self.lexed.line_starts)

    def _emit(self, kind: str, start: int, end: int) -> None:
        self.lexed.tokens.append(
            Token(kind=kind, text=self.src[start:end], start=start, end=end, line=self._line())
        )

    def _maybe_insert_semicolon(self, pos: int) -> None:
        """Implement Go's automatic semicolon insertion before a newline / EOF."""
        if not self.lexed.tokens:
            return
        last = self.lexed.tokens[-1]
        if last.kind in SEMICOLON_TRIGGER_KINDS:
            if last.kind != TOK_KEYWORD or last.text in SEMICOLON_TRIGGER_KEYWORDS:
                self.lexed.tokens.append(
                    Token(kind=TOK_SEMI, text=";", start=pos, end=pos, line=self._line())
                )
        elif last.kind == TOK_OP and last.text in SEMICOLON_TRIGGER_TOKENS:
            self.lexed.tokens.append(
                Token(kind=TOK_SEMI, text=";", start=pos, end=pos, line=self._line())
            )

    def _error(self, message: str, pos: int) -> None:
        self.lexed.errors.append(f"line {self.lexed.line_of(pos)}: {message}")

    def _note_newlines(self, start: int, end: int) -> None:
        """Register line starts for newlines inside ``[start, end)``.

        Raw strings and ``/* */`` comments are scanned in a single step, so the
        newlines they contain are invisible to the main loop.  Recording them here
        keeps ``line_of`` (and therefore every later token's line number) honest.
        """
        src = self.src
        pos = src.find("\n", start, end)
        while pos != -1:
            self.lexed.line_starts.append(pos + 1)
            pos = src.find("\n", pos + 1, end)

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        src, n = self.src, self.n
        i = 0
        while i < n:
            ch = src[i]
            if ch == "\n":
                self._maybe_insert_semicolon(i)
                self.lexed.line_starts.append(i + 1)
                i += 1
            elif ch in _WHITESPACE:
                i += 1
            elif ch == "/" and i + 1 < n:
                i = self._scan_comment_or_operator(i)
            elif ch == "`":
                i = self._scan_raw_string(i)
            elif ch == '"':
                i = self._scan_quoted(i, '"', TOK_STRING)
            elif ch == "'":
                i = self._scan_quoted(i, "'", TOK_CHAR)
            elif ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
                i = self._scan_number(i)
            elif _is_ident_start(ch):
                i = self._scan_ident(i)
            else:
                i = self._scan_operator(i)
        self._maybe_insert_semicolon(n)
        self.lexed.tokens.append(Token(kind=TOK_EOF, text="", start=n, end=n, line=self._line()))

    # -- scanners ---------------------------------------------------------

    def _scan_comment_or_operator(self, i: int) -> int:
        src, n = self.src, self.n
        nxt = src[i + 1]
        if nxt == "/":
            end = src.find("\n", i)
            if end == -1:
                end = n
            if end > i and src[end - 1] == "\r":
                end -= 1
            self.lexed.comments.append(
                Comment(start=i, end=end, block=False, line=self.lexed.line_of(i))
            )
            return end
        if nxt == "*":
            close = src.find("*/", i + 2)
            if close == -1:
                self._error("unterminated block comment", i)
                close = max(n - 2, i)
            end = min(close + 2, n)
            self.lexed.comments.append(
                Comment(start=i, end=end, block=True, line=self.lexed.line_of(i))
            )
            self._note_newlines(i, end)
            return end
        if nxt == "=":
            self._emit(TOK_OP, i, i + 2)
            return i + 2
        self._emit(TOK_OP, i, i + 1)
        return i + 1

    def _scan_raw_string(self, i: int) -> int:
        end = self.src.find("`", i + 1)
        if end == -1:
            self._error("unterminated raw string", i)
            end = self.n - 1
        self._emit(TOK_RAWSTRING, i, end + 1)
        self._note_newlines(i, end + 1)
        return end + 1

    def _scan_quoted(self, i: int, quote: str, kind: str) -> int:
        src, n = self.src, self.n
        j = i + 1
        while j < n:
            ch = src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                self._emit(kind, i, j + 1)
                return j + 1
            if ch == "\n":
                break
            j += 1
        self._error("unterminated string or rune literal", i)
        end = min(j + 1, n)
        self._emit(kind, i, end)
        self._note_newlines(i, end)
        return end

    def _scan_number(self, i: int) -> int:
        src, n = self.src, self.n
        j = i
        if src[j] == "0" and j + 1 < n and src[j + 1] in "xXbBoO":
            j += 2
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            return self._finish_number(i, j)
        seen_dot = False
        while j < n:
            ch = src[j]
            if ch.isdigit() or ch == "_":
                j += 1
            elif ch == "." and not seen_dot and not (j + 1 < n and src[j + 1] == "."):
                seen_dot = True
                j += 1
            elif ch in "eE" and j > i and not seen_dot:
                seen_dot = True
                j += 1
                if j < n and src[j] in "+-":
                    j += 1
            else:
                break
        return self._finish_number(i, j)

    def _finish_number(self, start: int, end: int) -> int:
        j = end
        if j < self.n and self.src[j] == "i":
            j += 1
        self._emit(TOK_NUMBER, start, j)
        return j

    def _scan_ident(self, i: int) -> int:
        src, n = self.src, self.n
        j = i
        while j < n and _is_ident_continue(src[j]):
            j += 1
        text = src[i:j]
        kind = TOK_KEYWORD if text in GO_KEYWORDS else TOK_IDENT
        self._emit(kind, i, j)
        return j

    def _scan_operator(self, i: int) -> int:
        src, n = self.src, self.n
        rest = src[i : i + 3]
        for size in (3, 2):
            candidate = rest[:size]
            if len(candidate) == size and candidate in _OPERATORS_BY_SIZE[size]:
                self._emit(TOK_OP, i, i + size)
                return i + size
        self._emit(TOK_OP, i, min(i + 1, n))
        return i + 1


_OPERATORS_BY_SIZE: dict[int, frozenset[str]] = {
    3: frozenset({"<<=", ">>=", "&^=", "..."}),
    2: frozenset(
        {
            "&&",
            "||",
            "<-",
            "++",
            "--",
            "==",
            "!=",
            "<=",
            ">=",
            ":=",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "&=",
            "|=",
            "^=",
            "<<",
            ">>",
            "&^",
        }
    ),
}


def _is_ident_start(ch: str) -> bool:
    """Bytes >= 0x80 are UTF-8 continuation bytes; treat them as identifier chars."""
    return (_IDENT_START_RE.match(ch) is not None) if ch.isascii() else True


def _is_ident_continue(ch: str) -> bool:
    return _is_ident_start(ch) or (ch.isascii() and ch.isdigit())


def base_type_name(text: str) -> str:
    """Extract ``Service`` from ``*Service``, ``[]Service`` or ``pkg.Service[T]``.

    Used to key methods by the type that declares them and to name embedded
    struct fields: both need the bare type name, without pointers, container
    syntax, package qualification or type arguments.
    """
    text = text.strip()
    text = text.rsplit("]", 1)[-1] or text
    text = text.lstrip("*").strip()
    text = text.split("[", 1)[0]
    return text.rsplit(".", 1)[-1].strip()

