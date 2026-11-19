"""Writing the machine-readable part of the index: atomic, deterministic files."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from aindex.constants import FILES_FILENAME, STATS_FILENAME, SYMBOLS_FILENAME
from aindex.model import FileRecord, Stats, SymbolRecord
from aindex.serialize import (
    ensure_directory,
    write_files,
    write_json,
    write_jsonl,
    write_stats,
    write_symbols,
    write_text,
)
from tests.support import IndexTestCase, read_json, read_text

#: Fields required by :class:`SymbolRecord`, with small defaults for the tests.
SYMBOL_DEFAULTS: dict[str, object] = {
    "id": "func:p:F",
    "kind": "func",
    "package": "p",
    "package_path": "p",
    "file": "p/p.go",
    "name": "F",
    "qualified_name": "F",
    "receiver": "",
    "signature": "func F()",
    "doc": "",
    "exported": True,
    "start_line": 1,
    "end_line": 1,
    "start_byte": 0,
    "end_byte": 1,
}


def make_symbol(**overrides: object) -> SymbolRecord:
    """A minimal :class:`SymbolRecord`; ``overrides`` win over the defaults."""
    return SymbolRecord(**{**SYMBOL_DEFAULTS, **overrides})  # type: ignore[arg-type]


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aindex-serialize-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)


class EnsureDirectoryTest(TempDirTest):
    def test_creates_nested_directories_and_returns_the_path(self) -> None:
        target = self.path("a", "b", "c")
        self.assertEqual(ensure_directory(target), target)
        self.assertTrue(os.path.isdir(target))

    def test_existing_directories_are_left_alone(self) -> None:
        target = self.path("a")
        ensure_directory(target)
        self.assertEqual(ensure_directory(target), target)
        self.assertTrue(os.path.isdir(target))


class WriteTextTest(TempDirTest):
    def test_writes_utf8_and_returns_the_path(self) -> None:
        target = self.path("note.md")
        self.assertEqual(write_text(target, "Grüße\n"), target)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), "Grüße\n".encode("utf-8"))

    def test_parent_directories_are_created(self) -> None:
        target = self.path("packages", "nested", "page.md")
        write_text(target, "x\n")
        self.assertTrue(os.path.isfile(target))

    def test_no_temporary_file_is_left_behind(self) -> None:
        write_text(self.path("file.txt"), "payload\n")
        self.assertEqual(os.listdir(self.root), ["file.txt"])

    def test_existing_content_is_replaced_completely(self) -> None:
        target = self.path("file.txt")
        write_text(target, "a much longer original content\n")
        write_text(target, "short\n")
        self.assertEqual(read_text(target), "short\n")

    def test_line_endings_are_lf(self) -> None:
        target = self.path("file.txt")
        write_text(target, "one\ntwo\n")
        with open(target, "rb") as handle:
            self.assertNotIn(b"\r", handle.read())


class WriteJsonTest(TempDirTest):
    def test_pretty_printed_with_a_trailing_newline(self) -> None:
        target = self.path("stats.json")
        write_json(target, {"a": [1, 2], "b": True})
        self.assertEqual(read_text(target), '{\n  "a": [\n    1,\n    2\n  ],\n  "b": true\n}\n')

    def test_non_ascii_is_not_escaped(self) -> None:
        target = self.path("stats.json")
        write_json(target, {"name": "Grüße"})
        self.assertIn("Grüße", read_text(target))
        self.assertEqual(read_json(target)["name"], "Grüße")


class WriteJsonlTest(TempDirTest):
    def test_returns_the_number_of_lines(self) -> None:
        records = [make_symbol(id="func:p:A", name="A"), make_symbol(id="func:p:B", name="B")]
        path = self.path("custom.jsonl")
        self.assertEqual(write_jsonl(path, records), 2)
        self.assertEqual(read_text(path).count("\n"), 2)

    def test_every_line_is_a_json_object(self) -> None:
        path = self.path("custom.jsonl")
        record = make_symbol(doc="one\ntwo")
        write_jsonl(path, [record])
        lines = read_text(path).splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), record.to_dict())

    def test_no_records_produces_an_empty_file(self) -> None:
        path = self.path("custom.jsonl")
        self.assertEqual(write_jsonl(path, []), 0)
        self.assertEqual(read_text(path), "")


class RecordJsonTest(unittest.TestCase):
    def test_symbol_json_is_a_single_line(self) -> None:
        record = make_symbol(doc="first line\nsecond line")
        line = record.to_json()
        self.assertNotIn("\n", line)
        payload = json.loads(line)
        self.assertEqual(payload["doc"], "first line\nsecond line")
        self.assertEqual(payload["id"], "func:p:F")
        self.assertEqual(payload["receiver_type"], "")
        self.assertEqual(payload["calls"], [])

    def test_symbol_json_keeps_the_documented_key_order(self) -> None:
        record = make_symbol()
        self.assertEqual(list(json.loads(record.to_json())), list(record.to_dict()))
        self.assertEqual(
            list(record.to_dict())[:4], ["id", "kind", "package", "package_path"]
        )

    def test_file_json_is_a_single_line(self) -> None:
        record = FileRecord(
            file="p/p.go", package="p", package_path="p", lines=2, symbols=1
        )
        line = record.to_json()
        self.assertNotIn("\n", line)
        self.assertEqual(list(json.loads(line)), list(record.to_dict()))

    def test_stats_json_rounds_the_duration(self) -> None:
        stats = Stats(generated_at="now", root="/x", duration_seconds=1.23456)
        self.assertEqual(stats.to_dict()["duration_seconds"], 1.235)
        self.assertEqual(stats.summary_lines()[-1], "duration: 1.23s")


class IndexFileTest(IndexTestCase):
    """The three machine-readable files of the demo project."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()

    def test_write_symbols_writes_the_demo_symbols(self) -> None:
        path = write_symbols(self.index_dir, self.result.symbols)
        self.assertEqual(path, os.path.join(self.index_dir, SYMBOLS_FILENAME))
        lines = read_text(path).splitlines()
        self.assertEqual(len(lines), len(self.result.symbols))
        records = [json.loads(line) for line in lines]
        self.assertEqual(records, [symbol.to_dict() for symbol in self.result.symbols])
        item = next(record for record in records if record["name"] == "Item")
        self.assertEqual(item["package_path"], "example.com/demo/internal/store")

    def test_write_files_writes_the_demo_files(self) -> None:
        path = write_files(self.index_dir, self.result.files)
        self.assertEqual(path, os.path.join(self.index_dir, FILES_FILENAME))
        lines = read_text(path).splitlines()
        self.assertEqual(len(lines), len(self.result.files))
        self.assertEqual(
            [json.loads(line) for line in lines],
            [record.to_dict() for record in self.result.files],
        )

    def test_write_stats_round_trips(self) -> None:
        path = write_stats(self.index_dir, self.result.stats)
        self.assertEqual(path, os.path.join(self.index_dir, STATS_FILENAME))
        self.assertEqual(read_json(path), self.result.stats.to_dict())

    def test_written_files_are_parsable_line_by_line(self) -> None:
        write_symbols(self.index_dir, self.result.symbols)
        write_files(self.index_dir, self.result.files)
        for name in (SYMBOLS_FILENAME, FILES_FILENAME):
            for line in read_text(os.path.join(self.index_dir, name)).splitlines():
                json.loads(line)  # raises when a record is not one JSON object per line

    def test_write_symbols_accepts_any_iterable(self) -> None:
        path = write_symbols(self.index_dir, iter(self.result.symbols))
        self.assertEqual(
            len(read_text(path).splitlines()), len(self.result.symbols)
        )

    def test_rewriting_a_record_file_is_idempotent(self) -> None:
        first = write_symbols(self.index_dir, self.result.symbols)
        before = read_text(first)
        write_symbols(self.index_dir, self.result.symbols)
        self.assertEqual(before, read_text(first))

    def test_no_temporary_files_remain_in_the_index_directory(self) -> None:
        write_symbols(self.index_dir, self.result.symbols)
        write_files(self.index_dir, self.result.files)
        write_stats(self.index_dir, self.result.stats)
        self.assertEqual(
            sorted(os.listdir(self.index_dir)),
            sorted([SYMBOLS_FILENAME, FILES_FILENAME, STATS_FILENAME]),
        )


if __name__ == "__main__":
    unittest.main()
