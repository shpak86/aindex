"""The hand-written Go lexer.

Everything else in the index is built by slicing the source the lexer hands back,
so these tests pin byte offsets, line numbers, token kinds and comment attachment
rather than Go's grammar.  The lexer never raises: broken input is reported through
``Lexed.errors`` and recovery is expected to keep the rest of the file usable.
"""

from __future__ import annotations

import dataclasses
import unittest

from aindex.constants import (
    LITERAL_KINDS,
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
from aindex.golex import base_type_name, lex
from aindex.goparse import parse_source

BLOCK_COMMENT_SOURCE = b"package p\n\n/* one\ntwo */\nfunc F() {}\n"
RAW_STRING_SOURCE = b"package p\n\nvar s = `a\nb\nc`\n\nfunc F() {}\n"


def kinds(source: bytes) -> list[tuple[str, str]]:
    """``(kind, text)`` for every token, including the inserted semicolons."""
    return [(token.kind, token.text) for token in lex(source).tokens]


def without_semis(source: bytes) -> list[tuple[str, str]]:
    """``(kind, text)`` pairs, hiding the noise of semicolons and EOF."""
    return [pair for pair in kinds(source) if pair[0] not in (TOK_SEMI, TOK_EOF)]


class TokenTest(unittest.TestCase):
    def test_is_op_matches_kind_and_text(self) -> None:
        tokens = lex(b"a == b").tokens
        self.assertTrue(tokens[1].is_op("==", ":="))
        self.assertFalse(tokens[1].is_op(":="))
        self.assertFalse(tokens[0].is_op("=="))

    def test_is_keyword_matches_kind_and_text(self) -> None:
        tokens = lex(b"func f() {}").tokens
        self.assertTrue(tokens[0].is_keyword("func", "var"))
        self.assertFalse(tokens[0].is_keyword("var"))
        self.assertFalse(tokens[1].is_keyword("func"))

    def test_text_is_always_the_scanned_slice(self) -> None:
        source = "var π = 1\n".encode("utf-8")
        for token in lex(source).tokens[:-1]:
            if token.kind == TOK_SEMI:  # inserted, so it has an empty span
                continue
            self.assertEqual(
                token.text.encode("latin-1"), source[token.start : token.end]
            )

    def test_tokens_are_frozen(self) -> None:
        token = lex(b"x").tokens[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            token.text = "y"  # type: ignore[misc]


class TokenKindTest(unittest.TestCase):
    def test_keywords_identifiers_and_operators(self) -> None:
        self.assertEqual(
            without_semis(b"func Foo_bar1(_x int)"),
            [
                (TOK_KEYWORD, "func"),
                (TOK_IDENT, "Foo_bar1"),
                (TOK_OP, "("),
                (TOK_IDENT, "_x"),
                (TOK_IDENT, "int"),
                (TOK_OP, ")"),
            ],
        )

    def test_a_keyword_prefix_is_still_an_identifier(self) -> None:
        self.assertEqual(kinds(b"ifx")[0], (TOK_IDENT, "ifx"))

    def test_number_literals(self) -> None:
        source = b"0x1f 0b1010 1_000 3.14 1e9 2i .5"
        self.assertEqual(
            [token.text for token in lex(source).tokens if token.kind == TOK_NUMBER],
            ["0x1f", "0b1010", "1_000", "3.14", "1e9", "2i", ".5"],
        )

    def test_string_rune_and_raw_literals(self) -> None:
        source = b'a := "x\\ty"\nb := \'z\'\nc := `raw`\n'
        self.assertEqual(
            [
                (token.kind, token.text)
                for token in lex(source).tokens
                if token.kind in LITERAL_KINDS
            ],
            [(TOK_STRING, '"x\\ty"'), (TOK_CHAR, "'z'"), (TOK_RAWSTRING, "`raw`")],
        )

    def test_operators_are_matched_longest_first(self) -> None:
        for text in ("<<=", ">>=", "&^=", "...", "<-", "&&", "||", "++", "--", "&^", "==", ":="):
            with self.subTest(op=text):
                tokens = [
                    token for token in lex(text.encode()).tokens if token.kind == TOK_OP
                ]
                self.assertEqual(
                    [(token.text, token.end - token.start) for token in tokens],
                    [(text, len(text))],
                )

    def test_a_single_slash_is_division(self) -> None:
        self.assertEqual(
            without_semis(b"a / b // c\n"),
            [(TOK_IDENT, "a"), (TOK_OP, "/"), (TOK_IDENT, "b")],
        )

    def test_unicode_identifiers_keep_byte_offsets(self) -> None:
        source = "héllo π".encode("utf-8")
        lexed = lex(source)
        idents = [token for token in lexed.tokens if token.kind == TOK_IDENT]
        self.assertEqual(
            [lexed.text(token.start, token.end) for token in idents],
            ["héllo", "π"],
        )
        self.assertEqual(idents[1].start, len("héllo ".encode("utf-8")))
        self.assertEqual(idents[1].text.encode("latin-1"), source[idents[1].start :])


class OffsetTest(unittest.TestCase):
    def test_empty_source_has_one_line_and_only_eof(self) -> None:
        lexed = lex(b"")
        self.assertEqual(
            [(token.kind, token.start, token.end, token.line) for token in lexed.tokens],
            [(TOK_EOF, 0, 0, 1)],
        )
        self.assertEqual((lexed.comments, lexed.errors, lexed.src), ([], [], ""))
        self.assertEqual(lexed.line_count, 1)

    def test_offsets_are_byte_offsets(self) -> None:
        source = "// café\nvar π = 1\n".encode("utf-8")
        lexed = lex(source)
        self.assertEqual(lexed.text(3, 8), "café")
        self.assertEqual((lexed.comments[0].start, lexed.comments[0].end), (0, 8))

        ident = next(
            token
            for token in lexed.tokens
            if lexed.text(token.start, token.end) == "π"
        )
        self.assertEqual(
            (ident.kind, ident.start, ident.end, ident.line), (TOK_IDENT, 13, 15, 2)
        )
        self.assertEqual(lexed.text(13, 12), "")

    def test_line_lookup(self) -> None:
        lexed = lex(b"a\nbb\n")
        self.assertEqual(
            [lexed.line_of(pos) for pos in (0, 1, 2, 3, 5)], [1, 1, 2, 2, 3]
        )
        self.assertEqual(lexed.line_count, 3)

    def test_line_count_without_a_trailing_newline(self) -> None:
        self.assertEqual(lex(b"a\nb").line_count, 2)

    def test_text_without_comments_blanks_comments_and_collapses_space(self) -> None:
        source = b"func f( /* c */ a int ) {}\n"
        lexed = lex(source)
        self.assertEqual(
            lexed.text_without_comments(0, len(source)), "func f( a int ) {}"
        )


class SemicolonInsertionTest(unittest.TestCase):
    def test_inserted_at_end_of_line_and_end_of_file(self) -> None:
        self.assertEqual(
            kinds(b"a\n"), [(TOK_IDENT, "a"), (TOK_SEMI, ";"), (TOK_EOF, "")]
        )
        self.assertEqual(
            kinds(b"a"), [(TOK_IDENT, "a"), (TOK_SEMI, ";"), (TOK_EOF, "")]
        )

    def test_not_inserted_after_an_operator(self) -> None:
        self.assertEqual(
            kinds(b"a +\nb\n"),
            [
                (TOK_IDENT, "a"),
                (TOK_OP, "+"),
                (TOK_IDENT, "b"),
                (TOK_SEMI, ";"),
                (TOK_EOF, ""),
            ],
        )

    def test_inserted_after_closing_brackets_and_increment(self) -> None:
        for line in (b"f(x)\n", b"a[1]\n", b"}\n", b"i++\n", b"i--\n"):
            with self.subTest(line=line):
                self.assertIn((TOK_SEMI, ";"), kinds(line))

    def test_only_statement_ending_keywords_trigger_insertion(self) -> None:
        for keyword in (b"return", b"break", b"continue", b"fallthrough"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    kinds(keyword + b"\n"),
                    [(TOK_KEYWORD, keyword.decode()), (TOK_SEMI, ";"), (TOK_EOF, "")],
                )
        for keyword in (b"func", b"if", b"var", b"type", b"package"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    kinds(keyword + b"\n"),
                    [(TOK_KEYWORD, keyword.decode()), (TOK_EOF, "")],
                )

    def test_a_trailing_comment_does_not_suppress_insertion(self) -> None:
        self.assertEqual(
            kinds(b"a // c\n"), [(TOK_IDENT, "a"), (TOK_SEMI, ";"), (TOK_EOF, "")]
        )


class CommentTest(unittest.TestCase):
    def test_line_comment_stops_before_the_newline(self) -> None:
        lexed = lex(b"// hi\nx\n")
        self.assertEqual(
            [(c.start, c.end, c.block, c.line) for c in lexed.comments],
            [(0, 5, False, 1)],
        )

    def test_carriage_return_is_not_part_of_a_line_comment(self) -> None:
        lexed = lex(b"// hi\r\nx\r\n")
        self.assertEqual((lexed.comments[0].start, lexed.comments[0].end), (0, 5))

    def test_block_comment_spans_to_the_closing_marker(self) -> None:
        lexed = lex(b"/* a\nb */x")
        self.assertEqual(len(lexed.comments), 1)
        self.assertEqual(
            (lexed.comments[0].start, lexed.comments[0].end, lexed.comments[0].block),
            (0, 9, True),
        )
        self.assertEqual(lexed.line_of(9), 2)

    def test_comments_are_not_tokens(self) -> None:
        self.assertEqual(without_semis(b"a // hi\n"), [(TOK_IDENT, "a")])
        self.assertEqual(without_semis(b"a /* hi */\n"), [(TOK_IDENT, "a")])


class DocCommentTest(unittest.TestCase):
    def doc_before(self, source: bytes, marker: bytes) -> tuple[str, int | None]:
        lexed = lex(source)
        return lexed.doc_before(lexed.src.index(marker.decode()))

    def test_a_comment_directly_above_is_attached(self) -> None:
        self.assertEqual(
            self.doc_before(b"// Doc.\nfunc F() {}\n", b"func"), ("Doc.", 0)
        )

    def test_a_blank_line_detaches_the_comment(self) -> None:
        self.assertEqual(
            self.doc_before(b"// Doc.\n\nfunc F() {}\n", b"func"), ("", None)
        )

    def test_consecutive_line_comments_form_one_group(self) -> None:
        self.assertEqual(
            self.doc_before(b"// One.\n// Two.\nfunc F() {}\n", b"func"),
            ("One.\nTwo.", 0),
        )

    def test_a_far_away_comment_is_not_pulled_in(self) -> None:
        source = b"// Far.\nvar x = 1\n\nfunc F() {}\n"
        self.assertEqual(self.doc_before(source, b"func"), ("", None))

    def test_an_indented_block_comment_is_dedented(self) -> None:
        source = b"/*\n\tOne.\n\t  Two.\n*/\nfunc F() {}\n"
        self.assertEqual(self.doc_before(source, b"func"), ("One.\n  Two.", 0))

    def test_no_comments_at_all(self) -> None:
        lexed = lex(b"func F() {}\n")
        self.assertEqual(lexed.doc_before(0), ("", None))

    def test_doc_for_range_finds_a_comment_that_closes_the_range(self) -> None:
        source = b"const (\n\tA = 1 // A.\n)\n"
        lexed = lex(source)
        self.assertEqual(lexed.doc_for_range(8, 21), "A.")
        self.assertEqual(lexed.doc_for_range(8, 19), "")
        self.assertEqual(lexed.doc_for_range(19, 21), "")


class MultiLineTokenLineNumberTest(unittest.TestCase):
    """``/* */`` comments and raw strings may span lines.

    Their newlines are scanned in one step, so a lexer that forgets to record them
    reports line numbers that are too small for everything that follows - which
    reaches the index as a wrong ``lines N-M`` range.
    """

    def test_lines_after_a_block_comment(self) -> None:
        lexed = lex(BLOCK_COMMENT_SOURCE)
        self.assertEqual(lexed.line_count, BLOCK_COMMENT_SOURCE.count(b"\n") + 1)
        self.assertEqual(
            [
                (token.text, token.line)
                for token in lexed.tokens
                if token.text in ("package", "func")
            ],
            [("package", 1), ("func", 5)],
        )
        self.assertEqual(lexed.comments[0].line, 3)

    def test_lines_after_a_raw_string(self) -> None:
        lexed = lex(RAW_STRING_SOURCE)
        self.assertEqual(lexed.line_count, RAW_STRING_SOURCE.count(b"\n") + 1)
        self.assertEqual(lexed.line_of(lexed.src.index("func")), 7)
        raw = next(token for token in lexed.tokens if token.kind == TOK_RAWSTRING)
        self.assertEqual((raw.text, raw.line), ("`a\nb\nc`", 3))

    def test_a_declaration_after_a_block_comment_keeps_its_line(self) -> None:
        parsed = parse_source(BLOCK_COMMENT_SOURCE, rel_path="p.go")
        self.assertEqual(parsed.lines, 6)
        self.assertEqual([decl.name for decl in parsed.decls], ["F"])
        self.assertEqual((parsed.decls[0].start_line, parsed.decls[0].end_line), (5, 5))

    def test_a_declaration_after_a_raw_string_keeps_its_line(self) -> None:
        parsed = parse_source(RAW_STRING_SOURCE, rel_path="p.go")
        self.assertEqual(parsed.lines, 8)
        self.assertEqual([decl.name for decl in parsed.decls], ["s", "F"])
        self.assertEqual((parsed.decls[1].start_line, parsed.decls[1].end_line), (7, 7))


class RecoveryTest(unittest.TestCase):
    def test_unterminated_block_comment_is_reported(self) -> None:
        lexed = lex(b"/* open\nx\n")
        self.assertEqual(lexed.errors, ["line 1: unterminated block comment"])
        self.assertEqual(len(lexed.comments), 1)

    def test_unterminated_raw_string_is_reported(self) -> None:
        lexed = lex(b"x := `open\n")
        self.assertEqual(lexed.errors, ["line 1: unterminated raw string"])

    def test_unterminated_string_is_reported_and_the_next_line_survives(self) -> None:
        lexed = lex(b'a := "open\nb := 1\n')
        self.assertEqual(lexed.errors, ["line 1: unterminated string or rune literal"])
        following = next(
            token for token in lexed.tokens if lexed.text(token.start, token.end) == "b"
        )
        self.assertEqual(following.line, 2)

    def test_a_broken_file_still_yields_tokens(self) -> None:
        for source in (b"/* open", b"`open", b'"open', b"'", b"a +"):
            with self.subTest(source=source):
                self.assertTrue(lex(source).tokens)


class BaseTypeNameTest(unittest.TestCase):
    def test_pointers_containers_and_qualifiers_are_stripped(self) -> None:
        for text, expected in (
            ("*Service", "Service"),
            ("[]Service", "Service"),
            ("pkg.Service", "Service"),
            (" Service ", "Service"),
            ("pkg.Service[T]", "Service"),
            ("", ""),
        ):
            with self.subTest(text=text):
                self.assertEqual(base_type_name(text), expected)


if __name__ == "__main__":
    unittest.main()

