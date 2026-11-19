"""Offline Go project indexer used by AI coding agents.

The package builds a hierarchical, symbol-based index of a Go code base:

* :mod:`aindex.discovery` -- walk the tree, honour ``.gitignore``, find packages
* :mod:`aindex.golex` -- dependency-free Go lexer with exact byte offsets
* :mod:`aindex.goparse` -- declaration/symbol extraction on top of the lexer
* :mod:`aindex.symbols` -- symbols, stable ids, doc comments, signatures
* :mod:`aindex.calls` -- approximate direct call/caller graph
* :mod:`aindex.model` -- index data model (dataclasses)
* :mod:`aindex.render` -- Markdown rendering
* :mod:`aindex.serialize` -- JSONL / stats serialization
* :mod:`aindex.pipeline` -- run all of the above for one project root
* :mod:`aindex.cli` -- command line interface

Typical use::

    from aindex import IndexOptions, run

    result, written = run(IndexOptions(root="/path/to/go/project"))
    print(result.stats.summary_lines())

:func:`aindex.pipeline.build_index` stops before writing anything, which is
what tests and other tools want.
"""

from __future__ import annotations

from .model import FileRecord, IndexResult, PackageInfo, Stats, SymbolRecord
from .pipeline import IndexOptions, build_index, run, write_index

__all__ = [
    "FileRecord",
    "IndexOptions",
    "IndexResult",
    "PackageInfo",
    "Stats",
    "SymbolRecord",
    "__version__",
    "build_index",
    "run",
    "write_index",
]

__version__ = "1.0.0"

