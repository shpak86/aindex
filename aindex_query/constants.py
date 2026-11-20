"""Shared constants for the read-only query CLI.

The filenames mirror :mod:`aindex.constants`; they are duplicated on purpose so
that ``aindex_query`` stays importable without pulling in the indexer.
"""

from __future__ import annotations

# --- Index layout ------------------------------------------------------------

DEFAULT_INDEX_DIRNAME = ".ai-index"
INDEX_DIR_ENV = "AINDEX_INDEX_DIR"

PROJECT_FILENAME = "PROJECT.md"
PACKAGES_DIRNAME = "packages"
SYMBOLS_FILENAME = "symbols.jsonl"
FILES_FILENAME = "files.jsonl"
STATS_FILENAME = "stats.json"

#: Files ``doctor`` insists on; a missing one means a broken index.
REQUIRED_INDEX_FILES: tuple[str, ...] = (
    PROJECT_FILENAME,
    SYMBOLS_FILENAME,
    FILES_FILENAME,
    STATS_FILENAME,
)

# --- Symbol kinds ------------------------------------------------------------

KIND_PACKAGE = "package"
KIND_STRUCT = "struct"
KIND_INTERFACE = "interface"
KIND_TYPE = "type"
KIND_ALIAS = "alias"
KIND_FUNC = "func"
KIND_TEST = "test"
KIND_METHOD = "method"
KIND_CONST = "const"
KIND_VAR = "var"

#: Every ``kind`` the indexer can emit, in the order used for stable sorting.
KIND_ORDER: tuple[str, ...] = (
    KIND_STRUCT,
    KIND_INTERFACE,
    KIND_TYPE,
    KIND_ALIAS,
    KIND_FUNC,
    KIND_TEST,
    KIND_METHOD,
    KIND_CONST,
    KIND_VAR,
    KIND_PACKAGE,
)

#: Kinds that ``search --kind`` accepts (``package`` included for completeness).
SEARCHABLE_KINDS: frozenset[str] = frozenset(KIND_ORDER)

#: Kinds treated as "callable" when picking entry points on the overview page.
CALLABLE_KINDS: tuple[str, ...] = (KIND_FUNC, KIND_METHOD, KIND_TEST)

#: Kinds that carry a method set / field list worth printing.
TYPED_KINDS: tuple[str, ...] = (KIND_STRUCT, KIND_INTERFACE, KIND_TYPE, KIND_ALIAS)

#: Kinds whose names are the type's exported surface on the overview page.
IMPORTANT_KINDS: tuple[str, ...] = (
    KIND_STRUCT,
    KIND_INTERFACE,
    KIND_TYPE,
    KIND_FUNC,
)

# --- Output limits -----------------------------------------------------------

DEFAULT_SEARCH_LIMIT = 10
DEFAULT_EDGES_LIMIT = 50
DEFAULT_CANDIDATE_LIMIT = 50
OVERVIEW_KEY_SYMBOLS = 15
PACKAGE_SYMBOL_LIMIT = 200
MAX_DOCTOR_EXAMPLES = 10

#: ``read`` never returns more than this many lines at once (``--limit 0`` for
#: the whole file); a symbol's body is usually far shorter.
DEFAULT_READ_LINES = 400

#: A doc string is folded to one line for list output at this width.
DOC_PREVIEW_CHARS = 220
