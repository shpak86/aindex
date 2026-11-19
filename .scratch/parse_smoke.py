"""Scratch smoke test for the pure-Python Go declaration parser.

Run: python .scratch/parse_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aindex import goparse

SRC = b"""//go:build linux && amd64
// +build linux,amd64

// Package billing handles orders.
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
\t*Logger
\tsync.Mutex
\tCache [4]byte
}

// Repository stores orders.
type Repository[T any] interface {
\tGet(ctx context.Context, id string) (T, error)
\tSave(T) error
\tfmt.Stringer
}

type Number = int64

type Handler func(w http.ResponseWriter, r *http.Request) error

// Create makes an order.
func (s *Service) Create(ctx context.Context, o Order) (Result, error) {
\treturn Result{}, nil
}

func Map[T, U any](in []T, f func(T) U) (out []U) { return nil }

func Variadic(prefix string, args ...any) {}

func Multi(a, b int, c string) (int, error)

//go:noinline
func Assembly()

func helper() {}
"""


def main() -> int:
    parsed = goparse.parse_source(SRC, "billing/billing.go")
    print("errors:", parsed.errors)
    print("package:", parsed.package_name, "doc:", repr(parsed.package_doc[:40]))
    print("build tags:", parsed.build_tags)
    print("imports:", [(i.alias, i.path) for i in parsed.imports])
    print("decls:", len(parsed.decls))
    for decl in parsed.decls:
        extra = ""
        if decl.params:
            extra += f" params={[p.to_dict() for p in decl.params]}"
        if decl.results:
            extra += f" results={decl.results!r}"
        if decl.type_params:
            extra += f" tparams={decl.type_params}"
        if decl.receiver:
            extra += f" recv={decl.receiver!r} recvvar={decl.receiver_var!r} recvtype={decl.receiver_type!r}"
        if decl.type_kind:
            extra += f" type_kind={decl.type_kind}"
        if decl.fields:
            extra += f" fields={[(f.name, f.type, f.embedded, f.tag) for f in decl.fields]}"
        if decl.iface_methods:
            extra += f" iface={[(m.name, m.signature) for m in decl.iface_methods]}"
        print(f"  {decl.kind:<9} {decl.name:<12} L{decl.start_line}-{decl.end_line} sig={decl.signature!r}{extra}")
    assert not parsed.errors, parsed.errors
    check_robustness(SRC)
    check_edge_cases()
    return 0


def check_robustness(src: bytes) -> None:
    """Truncating the source anywhere must never raise."""
    for cut in range(len(src) + 1):
        goparse.parse_source(src[:cut], "billing/billing.go")
    for junk in (b"", b"package", b"package 123", b"\xff\xfe\x00", b"func () {}", b"type {", b"}{"):
        goparse.parse_source(junk, "junk/junk.go")
    print("robustness: ok")


EDGE = b"""package edge

import "sync"

type Box[T any] struct {
\tMu      sync.Mutex
\tItems   []T
\tIndex   map[string]T
\tBase    *Base
\tGeneric Wrapper[T]
\tNested  struct {
\t\tInner int `json:"inner"`
\t}
\tCallback func(a int) error
\tCh       chan<- []byte
\tv, w     string
}

type Base struct{}

type Wrapper[T any] struct{ Value T }

type Sizer interface {
\tLen() int
\tString() string
\tfmt.Stringer
\t~int | ~string
}

type Alias = map[string]int

const (
\tA = iota
\tB
\tC int = iota * 2
\tD
)

var (
\tX, Y int = 1, 2
\tZ        = "z"
)

func (b Box[T]) Value() T { return b.Items[0] }

func (b *Box[T]) Push(items ...T) (n int, err error) { return 0, nil }

func NewBox[T any]() *Box[T] { return &Box[T]{} }

func init() {}

func noResults() {}

func (b Box[T]) unexported(a int, _ string) {}
"""


def check_edge_cases() -> None:
    parsed = goparse.parse_source(EDGE, "edge/edge.go")
    print("edge errors:", parsed.errors)
    assert not parsed.errors, parsed.errors
    by_name = {decl.name: decl for decl in parsed.decls}
    box = by_name["Box"]
    print("box fields:", [(f.name, f.type, f.embedded, f.tag) for f in box.fields])
    print("box kind:", box.type_kind, "tparams:", box.type_params)
    sizer = by_name["Sizer"]
    print("sizer methods:", [m.name for m in sizer.iface_methods])
    for name in ("A", "B", "C", "D"):
        print(name, by_name[name].value_type, repr(by_name[name].signature))
    print("X:", by_name["X"].value_type, "Z:", by_name["Z"].value_type)
    print("method Push:", by_name["Push"].receiver, by_name["Push"].receiver_var,
          by_name["Push"].results, [p.to_dict() for p in by_name["Push"].params])
    print("init:", by_name["init"].kind, "unexported:", by_name["unexported"].exported)



if __name__ == "__main__":
    raise SystemExit(main())
