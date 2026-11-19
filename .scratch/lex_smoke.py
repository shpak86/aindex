"""Scratch smoke test for the Go lexer.

Run: python .scratch/lex_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aindex import golex

SRC = b"""// Package billing handles orders.
package billing

import (
\t"context"
\tpay "example.com/app/internal/payment"
)

// MaxRetries is the retry budget.
const MaxRetries = 3

const (
\t// StatusNew means new.
\tStatusNew Status = iota
\tStatusDone
)

// Service orchestrates orders.
type Service struct {
\tGateway pay.Gateway `json:"gw"` // the gateway
\tid      string
}

// Create makes an order.
func (s *Service) Create(ctx context.Context, o Order) (Result, error) {
\treturn Result{}, nil
}

type Number interface {
\tInt() int
}

func Map[T any, U any](in []T, f func(T) U) []U { return nil }

var _ = `raw
string`
"""


def main() -> int:
    lexed = golex.lex(SRC)
    print("errors:", lexed.errors)
    print("lines:", lexed.line_count, "tokens:", len(lexed.tokens), "comments:", len(lexed.comments))
    for token in lexed.tokens[:16]:
        print(f"  {token.kind:<8} {token.text!r:<20} {token.start}-{token.end} line {token.line}")

    for marker in (b"const MaxRetries", b"func (s *Service)", b"type Number", b"type Service"):
        index = lexed.src.encode("latin-1").find(marker)
        print(f"doc before {marker!r}: {lexed.doc_before(index)[0]!r}")

    start = lexed.src.index("func (s *Service)")
    brace = lexed.src.index("{", start)
    print("normalized signature:", lexed.text_without_comments(start, brace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
