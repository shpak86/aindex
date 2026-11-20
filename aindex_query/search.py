"""Deterministic ranking and filtering over the symbol index.

No index, no embeddings, no fuzzy library: a symbol gets a score purely from
*where* the query matched.  The scale is fixed and documented so that two runs
on the same index and query produce byte-identical output.  There are three
tiers, and a symbol keeps its best hit across all of them:

===  ======  ===========================================================
1.00  exact   ``id``; then ``qualified_name`` 0.98, ``name`` 0.96,
              ``receiver`` 0.80, ``receiver_type`` 0.79
0.90  prefix  ``name``; then ``qualified_name`` 0.88, ``receiver_type``
              0.76, ``receiver`` 0.75, ``id`` 0.70
0.62  partial ``name``; then ``qualified_name`` 0.60, ``signature`` 0.58,
              ``doc`` 0.52, ``receiver_type``/``receiver`` 0.50/0.49,
              ``fields``/``methods``/``types`` 0.46, ``unresolved_calls``
              0.44, ``calls``/``callers`` 0.42, ``package`` 0.40,
              ``package_path`` 0.39, ``file`` 0.34, ``id`` 0.30
===  ======  ===========================================================

A multi-word query is scored per token: the average of the best field weight of
each token, scaled by coverage (how many tokens matched at all) so that a
partially matching symbol never outranks a full one.  Ties are broken by
exported-first, then kind order, name and id, so the output never shuffles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .models import SearchResult, Symbol

#: Identity fields: a *full* match is worth the listed weight.
EXACT_PATHS: tuple[tuple[str, float], ...] = (
    ("id", 1.00),
    ("qualified_name", 0.98),
    ("name", 0.96),
    ("receiver", 0.80),
    ("receiver_type", 0.79),
)

#: Identity fields again, scored lower when the query is only a prefix.
PREFIX_PATHS: tuple[tuple[str, float], ...] = (
    ("name", 0.90),
    ("qualified_name", 0.88),
    ("receiver_type", 0.76),
    ("receiver", 0.75),
    ("id", 0.70),
)

#: Prose and relation fields: any occurrence counts, so the weights stay low --
#: an ``id`` containing the query (``func:pkg:NewTunnel``) must never beat the
#: symbol actually named that.
CONTAINS_PATHS: tuple[tuple[str, float], ...] = (
    ("name", 0.62),
    ("qualified_name", 0.60),
    ("signature", 0.58),
    ("doc", 0.52),
    ("receiver_type", 0.50),
    ("receiver", 0.49),
    ("fields", 0.46),
    ("methods", 0.46),
    ("types", 0.46),
    ("unresolved_calls", 0.44),
    ("calls", 0.42),
    ("callers", 0.42),
    ("package", 0.40),
    ("package_path", 0.39),
    ("file", 0.34),
    ("id", 0.30),
)

#: Every searchable field, once, in a stable order.
FIELD_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        name for table in (EXACT_PATHS, PREFIX_PATHS, CONTAINS_PATHS) for name, _ in table
    )
)

#: Multi-word scores are damped so a single-token match still wins.
_MULTIWORD_DAMPING = 0.9


@dataclass(slots=True)
class SearchFilters:
    """Everything ``search`` can narrow the result set with."""

    kinds: tuple[str, ...] = ()
    package_path: str = ""
    file: str = ""
    exported_only: bool = False
    include_tests: bool = True


@dataclass(slots=True)
class SearchOutcome:
    """The ranked hits of one query, plus the total before the limit."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total: int = 0
    limit: int = 0

    @property
    def truncated(self) -> bool:
        return self.limit > 0 and self.total > len(self.results)


def _field_text(symbol: Symbol, name: str) -> str:
    """Lower-cased text of one searchable field (list fields are joined)."""
    value = getattr(symbol, name, "")
    if isinstance(value, (list, tuple)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value)
    return text.lower()


def matches_filters(symbol: Symbol, filters: SearchFilters) -> bool:
    """Whether ``symbol`` survives every filter that was actually set."""
    if filters.kinds and symbol.kind not in filters.kinds:
        return False
    if filters.package_path and symbol.package_path != filters.package_path:
        return False
    if filters.file and symbol.file != filters.file:
        return False
    if filters.exported_only and not symbol.exported:
        return False
    if not filters.include_tests and (symbol.test_related or symbol.kind == "test"):
        return False
    return True


def _symbol_texts(symbol: Symbol) -> dict[str, str]:
    """Lower-cased text of every searchable field, computed once per symbol."""
    return {name: _field_text(symbol, name) for name in FIELD_NAMES}


def _best_token_score(token: str, texts: Mapping[str, str]) -> float:
    """Best score of one already-lower-cased token against a symbol's texts."""
    best = 0.0
    for name, weight in EXACT_PATHS:
        if texts.get(name) == token:
            best = max(best, weight)
    for name, weight in PREFIX_PATHS:
        text = texts.get(name)
        if text and text.startswith(token):
            best = max(best, weight)
    for name, weight in CONTAINS_PATHS:
        text = texts.get(name)
        if text and token in text:
            best = max(best, weight)
    return best


def score_symbol(
    symbol: Symbol, query: str, tokens: Sequence[str] | None = None
) -> float:
    """Score one symbol against a query.

    A single-token query is scored directly.  A multi-word query is first tried
    as one phrase (so ``"closed applyMessage"`` can still hit a ``doc`` string)
    and then per token: the average of each token's best field score, scaled by
    the fraction of tokens that matched at all, so a symbol matching one word of
    three never outranks one matching all three.
    """
    parts = tuple(part for part in query.lower().split() if part) if tokens is None else tuple(tokens)
    if not parts:
        return 0.0
    texts = _symbol_texts(symbol)
    if len(parts) == 1:
        return _best_token_score(parts[0], texts)
    best = _best_token_score(" ".join(parts), texts)
    scores = [_best_token_score(part, texts) for part in parts]
    matched = sum(1 for value in scores if value > 0.0)
    if matched:
        average = sum(scores) / len(parts)
        coverage = matched / len(parts)
        best = max(best, average * (0.55 + 0.45 * coverage) * _MULTIWORD_DAMPING)
    return best


def _order_key(result: SearchResult) -> tuple[object, ...]:
    symbol = result.symbol
    return (
        -result.score,
        not symbol.exported,
        symbol.kind_rank,
        symbol.qualified_name.casefold(),
        symbol.id,
    )


def search(
    symbols: Iterable[Symbol],
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 0,
) -> SearchOutcome:
    """Rank every matching symbol; ``limit <= 0`` means "no limit"."""
    filters = filters or SearchFilters()
    text = query.strip()
    tokens = tuple(part for part in text.lower().split() if part)
    outcome = SearchOutcome(query=query, limit=limit)
    if not tokens:
        return outcome
    ranked: list[SearchResult] = []
    for symbol in symbols:
        if not matches_filters(symbol, filters):
            continue
        score = score_symbol(symbol, text, tokens)
        if score > 0.0:
            ranked.append(SearchResult(symbol=symbol, score=score))
    ranked.sort(key=_order_key)
    outcome.total = len(ranked)
    if limit > 0:
        ranked = ranked[:limit]
    outcome.results = ranked
    return outcome

