"""Shared constants for the Go index builder.

Keeping magic strings in one place makes the parsing and rendering code readable
and keeps the emitted index stable across releases.
"""

from __future__ import annotations

# --- Go lexing ---------------------------------------------------------------

GO_KEYWORDS: frozenset[str] = frozenset(
    """
    break case chan const continue default defer else fallthrough for func go goto
    if import interface map package range return select struct switch type var
    """.split()
)

# Tokens after which a newline triggers Go's automatic semicolon insertion.
SEMICOLON_TRIGGER_TOKENS: frozenset[str] = frozenset({")", "]", "}", "++", "--"})
SEMICOLON_TRIGGER_KEYWORDS: frozenset[str] = frozenset(
    {"break", "continue", "fallthrough", "return"}
)
# Token kinds that trigger semicolon insertion at end of line / end of file.
SEMICOLON_TRIGGER_KINDS: frozenset[str] = frozenset(
    {"ident", "keyword", "number", "string", "rawstring", "char"}
)

# --- Token kinds -------------------------------------------------------------

TOK_IDENT = "ident"
TOK_KEYWORD = "keyword"
TOK_NUMBER = "number"
TOK_STRING = "string"
TOK_RAWSTRING = "rawstring"
TOK_CHAR = "char"
TOK_OP = "op"
TOK_SEMI = "semi"
TOK_EOF = "eof"

LITERAL_KINDS: frozenset[str] = frozenset({TOK_NUMBER, TOK_STRING, TOK_RAWSTRING, TOK_CHAR})

# --- Declaration / symbol kinds ---------------------------------------------

KIND_PACKAGE = "package"
KIND_FILE = "file"
KIND_TYPE = "type"
KIND_STRUCT = "struct"
KIND_INTERFACE = "interface"
KIND_ALIAS = "alias"
KIND_FUNC = "func"
KIND_METHOD = "method"
KIND_CONST = "const"
KIND_VAR = "var"
KIND_FIELD = "field"
KIND_TEST = "test"

#: Order used when grouping symbols in Markdown output.
SYMBOL_KIND_ORDER: tuple[str, ...] = (
    KIND_STRUCT,
    KIND_INTERFACE,
    KIND_TYPE,
    KIND_ALIAS,
    KIND_FUNC,
    KIND_TEST,
    KIND_METHOD,
    KIND_CONST,
    KIND_VAR,
    KIND_FIELD,
)

#: Declaration kinds grouped into a single symbol (one symbol per declared name).
DECL_GROUP_KEYWORDS: frozenset[str] = frozenset({"const", "var", "type"})

# --- Go semantics ------------------------------------------------------------

#: Predeclared identifiers. These never resolve to a project symbol.
GO_PREDECLARED: frozenset[str] = frozenset(
    """
    any append bool byte cap clear close comparable complex complex64 complex128 copy
    delete error false float32 float64 imag int int8 int16 int32 int64 int uint
    uint8 uint16 uint32 uint64 uintptr iota len make max min new nil panic print
    println real recover rune string true uintptr
    """.split()
)

#: Types that are always available; used to skip conversions during call extraction.
GO_BUILTIN_TYPES: frozenset[str] = frozenset(
    """
    any bool byte comparable complex64 complex128 error float32 float64 int int8
    int16 int32 int64 rune string uint uint8 uint16 uint32 uint64 uintptr
    """.split()
)

#: The blank identifier: declarations using it declare nothing.
BLANK_IDENTIFIER = "_"

TEST_FILE_SUFFIX = "_test.go"
TEST_PACKAGE_SUFFIX = "_test"

#: Go test/benchmark/example entry points, checked as prefixes.
TEST_FUNC_PREFIXES: tuple[str, ...] = ("Test", "Benchmark", "Fuzz", "Example")

#: Comment markers that carry generated-file warnings.
GENERATED_CODE_MARKERS: tuple[str, ...] = (
    "Code generated",
    "DO NOT EDIT",
)

# --- Output ------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = ".ai-index"
DEFAULT_MAX_DOC_CHARS = 1200
DOC_TRUNCATION_SUFFIX = "..."
UNRESOLVED_CALL_PREFIX = "?"

STATS_FILENAME = "stats.json"
SYMBOLS_FILENAME = "symbols.jsonl"
FILES_FILENAME = "files.jsonl"
PROJECT_FILENAME = "PROJECT.md"
PACKAGES_DIRNAME = "packages"

#: Directories never indexed (module fixtures, build output, VCS metadata).
DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".ai-index",
        "vendor",
        "node_modules",
        "dist",
        "build",
        "bin",
        "testdata",
        "__pycache__",
    }
)

GO_MOD_FILENAME = "go.mod"
GITIGNORE_FILENAME = ".gitignore"
