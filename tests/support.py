"""Shared fixtures and helpers for the test suite.

The Go sources below are a small but realistic project ("demo"): a ``main``
package, an internal package pair with an interface, a generic type, an
external test package, build-tagged files that declare the same symbol, a file
with a syntax error, a file without a package clause and the usual directories
that must be skipped.

Everything is written to a temporary directory by :func:`make_project`, so the
tests never touch the repository they live in.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Mapping

from aindex.constants import DEFAULT_OUTPUT_DIR
from aindex.model import IndexResult, SymbolRecord
from aindex.pipeline import IndexOptions, build_index, run

GO_MOD = """module example.com/demo

go 1.21.5
"""

#: ``*.gen.go`` is generated code; ``keep.gen.go`` is the negation case.
GITIGNORE = """# generated code
*.gen.go
!keep.gen.go
/tmp/
"""

MAIN_GO = """// Command demo stores and reads one answer.
package main

import (
	"context"
	"fmt"

	"example.com/demo/internal/store"
)

func main() {
	if err := run(context.Background()); err != nil {
		fmt.Println(err)
	}
}

// run is the wiring used by main.
func run(ctx context.Context) error {
	items := store.New()
	item, err := items.Get(ctx, "answer")
	if err != nil {
		return err
	}
	_ = store.Item{ID: item.ID, Kind: store.KindAnswer}
	return nil
}
"""

STORE_GO = """// Package store keeps items in memory.
package store

import (
	"context"
	"fmt"
	"sync"
)

// Item is one stored value.
type Item struct {
	ID   string
	Kind Kind
}

// Kind classifies items.
type Kind string

// Item kinds.
const (
	KindAnswer Kind = "answer"
	KindNote   Kind = "note"
)

// Version of the store implementation.
const Version = "1.0.0"

// ErrMissing is returned for unknown ids.
var ErrMissing = fmt.Errorf("store: missing item")

// Store is the read side of the store.
type Store interface {
	// Get returns one item.
	Get(ctx context.Context, id string) (Item, error)
}

// memory is the in-process implementation of Store.
type memory struct {
	mu    sync.Mutex
	items map[string]Item
}

// New returns an empty in-memory store.
func New() *memory {
	return &memory{items: map[string]Item{}}
}

// Get implements Store.
func (m *memory) Get(ctx context.Context, id string) (Item, error) {
	item, ok := m.lookup(id)
	if !ok {
		return Item{}, ErrMissing
	}
	return item, nil
}

// Put stores one item.
func (m *memory) Put(item Item) { m.save(item) }

func (m *memory) lookup(id string) (Item, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	item, ok := m.items[id]
	return item, ok
}

func (m *memory) save(item Item) { m.items[item.ID] = item }

var _ Store = (*memory)(nil)
"""

STORE_TEST_GO = """package store

import (
	"context"
	"testing"
)

func TestMemoryGet(t *testing.T) {
	items := New()
	items.Put(Item{ID: "answer"})
	if _, err := items.Get(context.Background(), "answer"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
"""

API_GO = """// Package api adapts the store for other layers.
package api

import (
	"context"

	"example.com/demo/internal/store"
)

// List is a typed list.
type List[T any] struct {
	items []T
}

// Push appends one value.
func (l *List[T]) Push(value T) {
	l.items = append(l.items, value)
}

// Handler serves items.
type Handler struct {
	store store.Store
}

// NewHandler wires a handler to a store.
func NewHandler(s store.Store) *Handler {
	return &Handler{store: s}
}

// Get reads one item through the store.
func (h *Handler) Get(ctx context.Context, id string) (store.Item, error) {
	return h.store.Get(ctx, id)
}

// Map converts every item with f.
func Map[T any](items []T, f func(T) T) []T {
	out := make([]T, 0, len(items))
	for _, item := range items {
		out = append(out, f(item))
	}
	return out
}
"""

API_TEST_GO = """package api_test

import (
	"context"
	"testing"

	"example.com/demo/internal/api"
)

func TestHandlerGet(t *testing.T) {
	handler := api.NewHandler(nil)
	if _, err := handler.Get(context.Background(), "answer"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
"""

MODE_LINUX_GO = """//go:build linux

package platform

// Mode is the value used on Linux.
const Mode = "linux"
"""

MODE_WINDOWS_GO = """//go:build windows

package platform

// Mode is the value used on Windows.
const Mode = "windows"
"""

#: The lexer reports an error, but the parser still recovers declarations.
BROKEN_GO = """package broken

// BrokenValue has an unterminated string literal (on purpose).
var BrokenValue = "unterminated

// StillParsed is recovered after the broken literal.
func StillParsed() int { return 1 }
"""

#: No package clause at all: the file cannot be attributed to a package.
ORPHAN_GO = """// Orphan has no package clause on purpose.
func Orphan() {}
"""

KEEP_GEN_GO = """package main

// generated is hand written, but lives in a .gen.go file.
func generated() {}
"""

DEMO_FILES: dict[str, str] = {
    "go.mod": GO_MOD,
    ".gitignore": GITIGNORE,
    "main.go": MAIN_GO,
    "keep.gen.go": KEEP_GEN_GO,
    "docs/guide.md": "# demo\n\nNot Go.\n",
    "internal/store/store.go": STORE_GO,
    "internal/store/store_test.go": STORE_TEST_GO,
    "internal/api/api.go": API_GO,
    "internal/api/api_test.go": API_TEST_GO,
    "platform/mode_linux.go": MODE_LINUX_GO,
    "platform/mode_windows.go": MODE_WINDOWS_GO,
    "broken/broken.go": BROKEN_GO,
    "orphan/nopackage.go": ORPHAN_GO,
    "gen/db.gen.go": "package gen\n\n// DB is generated.\nfunc DB() {}\n",
    "tmp/cache.go": "package tmp\n\nfunc Cache() {}\n",
    "vendor/dep/dep.go": "package dep\n\nfunc Dep() {}\n",
    "testdata/sample.go": "package sample\n\nfunc Sample() {}\n",
    ".hidden/secret.go": "package hidden\n\nfunc Secret() {}\n",
}

#: Go files that must never end up in the index, and why.
IGNORED_FILES: tuple[str, ...] = (
    "gen/db.gen.go",  # .gitignore: *.gen.go
    "tmp/cache.go",  # .gitignore: /tmp/
    "vendor/dep/dep.go",  # DEFAULT_IGNORED_DIRS
    "testdata/sample.go",  # DEFAULT_IGNORED_DIRS
    ".hidden/secret.go",  # dot directory
)

#: Go files of :data:`DEMO_FILES` that are indexed, in discovery order.
INDEXED_FILES: tuple[str, ...] = (
    "broken/broken.go",
    "internal/api/api.go",
    "internal/api/api_test.go",
    "internal/store/store.go",
    "internal/store/store_test.go",
    "keep.gen.go",
    "main.go",
    "orphan/nopackage.go",
    "platform/mode_linux.go",
    "platform/mode_windows.go",
)

MODULE_PATH = "example.com/demo"



def write_tree(root: str, files: Mapping[str, str]) -> str:
    """Create every ``rel_path -> text`` entry below ``root``."""
    for rel_path, text in files.items():
        path = os.path.join(root, *rel_path.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return root


def make_project(directory: str, files: Mapping[str, str] = DEMO_FILES) -> str:
    """Write the demo project into ``directory`` and return its root."""
    return write_tree(os.path.join(directory, "demo"), files)


def index_dir_for(root: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> str:
    """Where the index of ``root`` is written."""
    return IndexOptions(root=root, output_dir=output_dir).resolved_output_dir()


def read_jsonl(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class IndexTestCase(unittest.TestCase):
    """Base class with a throwaway copy of :data:`DEMO_FILES`."""

    files: Mapping[str, str] = DEMO_FILES

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aindex-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = make_project(self._tmp.name, self.files)
        self.index_dir = index_dir_for(self.root)
        self.result: IndexResult
        self.written: list[str] = []

    # -- running the tool -------------------------------------------------
    def build(self, **options: Any) -> IndexResult:
        """Index the project without writing anything."""
        return build_index(IndexOptions(root=self.root, **options))

    def write_index(self, **options: Any) -> tuple[IndexResult, list[str]]:
        """Index the project and write ``.ai-index/``."""
        result, written = run(IndexOptions(root=self.root, **options))
        self.result, self.written = result, written
        return result, written

    # -- inspecting the result -------------------------------------------
    @staticmethod
    def by_id(result: IndexResult) -> dict[str, SymbolRecord]:
        return {record.id: record for record in result.symbols}

    def symbol(self, symbol_id: str, result: IndexResult | None = None) -> SymbolRecord:
        """The record with ``symbol_id``; fails the test when it is missing."""
        records = self.by_id(result if result is not None else self.result)
        self.assertIn(symbol_id, records, f"missing symbol {symbol_id}")
        return records[symbol_id]
