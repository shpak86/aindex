"""Smoke test for the symbol/call-graph layer (no filesystem needed).

Run: python3 .scratch/link_smoke.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from aindex.calls import SymbolIndex, SymbolLinks, link_symbols  # noqa: E402
from aindex.goparse import parse_source  # noqa: E402
from aindex.model import PackageInfo  # noqa: E402
from aindex.symbols import (  # noqa: E402
    body_span,
    disambiguate_ids,
    package_symbol,
    resolve_context,
    signature_refs,
    symbols_for_file,
)

MODULE = "example.com/m"

A_GO = b'''// Package a is a demo package.
package a

import (
"context"
"example.com/m/a/internal/helper"
)

// Service does things.
type Service struct {
// Store persists things.
Store Store
Logger *helper.Logger `json:"logger"`
context.Context
}

// Store is the persistence port.
type Store interface {
// Save stores one item.
Save(ctx context.Context, item Item) error
Name() string
Store
~int | string
}

// Item is an alias for a slice of bytes.
type Item = []byte

// Status is a defined string type.
type Status string

const (
// StatusOK is fine.
StatusOK Status = "ok"
StatusBroken
)

var ErrBroken = error(nil)

// New builds a Service.
func New() *Service {
return &Service{}
}

// Create creates an item.
func (s *Service) Create(ctx context.Context, name string) error {
return s.validate(name)
}

func (s *Service) validate(name string) error {
return helper.Wrap(ErrBroken, name)
}
'''

B_GO = b'''package b

import (
"example.com/m/a"
"example.com/m/b/internal/helper"
)

// Run exercises package a.
func Run() error {
s := a.New()
if err := s.Create(stubContext(), "x"); err != nil {
return helper.Wrap(err, "run")
}
_ = s
_ = a.StatusOK
_ = a.Service{Store: nil}
return nil
}

// List is generic.
func List[T any](items []T) []T {
out := make([]T, 0, len(items))
for _, item := range items {
out = append(out, item)
}
if len(out) > 0 && nop() {
return out
}
return out
}

func stubContext() context.Context { return nil }

func nop() bool { return false }
'''

def build(packages):
    """Parse every file, link every symbol; returns (records, stats, renamed)."""
    symbols = []
    package_rows = []
    links = []
    for (path, name), files in packages.items():
        package = PackageInfo(path=path, name=name, directory=path)
        for rel_path, raw in files:
            parsed = parse_source(raw, rel_path)
            if parsed.errors:
                raise AssertionError(f"{rel_path}: {parsed.errors}")
            rows = symbols_for_file(parsed, name, path)
            for decl, symbol in zip(parsed.decls, rows):
                links.append(
                    SymbolLinks(
                        symbol=symbol,
                        context=resolve_context(decl, parsed, path),
                        lexed=parsed.lexed,
                        body=body_span(decl),
                        signature_types=signature_refs(decl),
                    )
                )
            package.symbols.extend(rows)
            symbols.extend(rows)
        package_rows.append(package_symbol(package))
    renamed = disambiguate_ids(symbols)
    index = SymbolIndex()
    for symbol in symbols:
        index.add(symbol)
    stats = link_symbols(links, index)
    return package_rows + symbols, stats, renamed


PACKAGES = {
    (f"{MODULE}/a", "a"): [("a/a.go", A_GO)],
    (f"{MODULE}/b", "b"): [("b/b.go", B_GO)],
    (f"{MODULE}/c", "c"): [
        ("c/c_linux.go", b'package c\n\nfunc Mode() string { return "linux" }\n'),
        ("c/c_windows.go", b'package c\n\nfunc Mode() string { return "windows" }\n'),
    ],
}

CHECKS = [
    (f"func:{MODULE}/a:New", "types", [f"struct:{MODULE}/a:Service"]),
    (f"struct:{MODULE}/a:Service", "types", [f"interface:{MODULE}/a:Store"]),
    (f"method:{MODULE}/a:Service.Create", "calls", [f"method:{MODULE}/a:Service.validate"]),
    (f"method:{MODULE}/a:Service.validate", "calls", []),
    (f"method:{MODULE}/a:Service.validate", "unresolved_calls", ["helper.Wrap"]),
    (f"func:{MODULE}/b:Run", "calls", [f"func:{MODULE}/a:New", f"func:{MODULE}/b:stubContext"]),
    (f"func:{MODULE}/b:Run", "unresolved_calls", ["helper.Wrap", "s.Create"]),
    (f"func:{MODULE}/b:Run", "types", [f"struct:{MODULE}/a:Service"]),
    (f"func:{MODULE}/b:List", "calls", [f"func:{MODULE}/b:nop"]),
    (f"func:{MODULE}/a:New", "callers", [f"func:{MODULE}/b:Run"]),
    (f"method:{MODULE}/a:Service.validate", "callers", [f"method:{MODULE}/a:Service.Create"]),
    (f"const:{MODULE}/a:StatusOK", "types", [f"type:{MODULE}/a:Status"]),
    (f"const:{MODULE}/a:StatusBroken", "types", [f"type:{MODULE}/a:Status"]),
    (
        f"interface:{MODULE}/a:Store",
        "fields",
        ["Save(ctx context.Context, item Item) error", "Name() string"],
    ),
    (
        f"struct:{MODULE}/a:Service",
        "fields",
        ["Store Store", 'Logger *helper.Logger `json:"logger"`', "context.Context [embedded]"],
    ),
    (
        f"method:{MODULE}/a:Service.Create",
        "signature",
        "func (s *Service) Create(ctx context.Context, name string) error",
    ),
    (f"package:{MODULE}/a:a", "kind", "package"),
]

def main() -> None:
    records, stats, renamed = build(PACKAGES)
    by_id = {record.id: record for record in records}
    print(f"renamed ids: {renamed}")
    print(f"records: {len(records)}  link stats: {json.dumps(stats.to_dict(), sort_keys=True)}")
    for record in records:
        print(f"{record.id:<50} kind={record.kind:<9} lines={record.start_line}-{record.end_line}")
        for label in ("fields", "calls", "callers", "types", "unresolved_calls"):
            value = getattr(record, label)
            if value:
                print(f"    {label}: {value}")
    print()
    failures = 0
    for symbol_id, attribute, expected in CHECKS:
        record = by_id.get(symbol_id)
        if record is None:
            print(f"FAIL {symbol_id} not found")
            failures += 1
            continue
        actual = getattr(record, attribute)
        if actual == expected:
            print(f"ok   {symbol_id}.{attribute}")
        else:
            failures += 1
            print(f"FAIL {symbol_id}.{attribute}\n     actual   {actual}\n     expected {expected}")
    collisions = sorted(key for key in by_id if key.startswith(f"func:{MODULE}/c:Mode"))
    ok = len(collisions) == 2 and all("#c/c_" in key for key in collisions)
    failures += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} collision ids: {collisions}")
    print(f"\n{'ALL CHECKS PASSED' if not failures else str(failures) + ' FAILURE(S)'}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
