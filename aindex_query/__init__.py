"""Read-only queries against the ``.ai-index/`` written by :mod:`aindex`.

The indexer answers "what is in this Go project?" once, up front; this package
answers the follow-up questions an agent asks while working -- *where is this
symbol, who calls it, what does that package look like, show me the source of
this method* -- without re-parsing a single ``.go`` file.

Only the standard library is used, and nothing here ever writes to the index:
every command is a pure function of ``.ai-index/`` plus (for ``read``) one
source file.

* :mod:`aindex_query.config` -- locate the project root and the index
* :mod:`aindex_query.index_store` -- load ``symbols.jsonl`` / ``files.jsonl``
* :mod:`aindex_query.search` -- deterministic relevance ranking
* :mod:`aindex_query.source_reader` -- the one place a source file is opened
* :mod:`aindex_query.render` -- the human-readable text of every command
* :mod:`aindex_query.cli` -- argument parsing and exit codes

Typical use::

    from aindex_query import IndexStore, resolve_config

    store = IndexStore(resolve_config(root="/path/to/go/project"))
    for symbol in store.by_name["New"]:
        print(symbol.id, symbol.signature)

Or from a shell, which is the point::

    python3 aindex-query --root /path/to/go/project search "New store"
"""

from __future__ import annotations

from .config import Config, resolve_config
from .errors import (
    EXIT_INVALID_INDEX,
    EXIT_INDEX_NOT_FOUND,
    EXIT_OK,
    EXIT_SECURITY,
    EXIT_SOURCE_READ_ERROR,
    EXIT_SYMBOL_NOT_FOUND,
    EXIT_USAGE,
    IndexNotFoundError,
    InvalidIndexError,
    QueryError,
    SecurityError,
    SourceReadError,
    SymbolNotFoundError,
    UsageError,
)
from .index_store import IndexStore, PackageGroup
from .models import (
    CandidateList,
    FileRecord,
    IndexStats,
    ParseProblem,
    SearchResult,
    Symbol,
)
from .search import SearchFilters, SearchOutcome, search
from .source_reader import SourceSlice, read_slice

__all__ = [
    "CandidateList",
    "Config",
    "EXIT_INDEX_NOT_FOUND",
    "EXIT_INVALID_INDEX",
    "EXIT_OK",
    "EXIT_SECURITY",
    "EXIT_SOURCE_READ_ERROR",
    "EXIT_SYMBOL_NOT_FOUND",
    "EXIT_USAGE",
    "FileRecord",
    "IndexNotFoundError",
    "IndexStats",
    "IndexStore",
    "InvalidIndexError",
    "PackageGroup",
    "ParseProblem",
    "QueryError",
    "SearchFilters",
    "SearchOutcome",
    "SearchResult",
    "SecurityError",
    "SourceReadError",
    "SourceSlice",
    "Symbol",
    "SymbolNotFoundError",
    "UsageError",
    "__version__",
    "read_slice",
    "resolve_config",
    "search",
]

__version__ = "1.0.0"
