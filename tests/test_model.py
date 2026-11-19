"""The index data model.

The records are the JSONL contract an agent reads, so these tests pin the field
order that ends up in the files, the derived properties and the deterministic
ordering helpers.  How the records get filled is covered by ``test_pipeline``
and ``test_serialize``.
"""

from __future__ import annotations

import json
import unittest

from aindex.constants import KIND_FUNC, KIND_METHOD, KIND_PACKAGE, KIND_STRUCT
from aindex.model import (
    FileRecord,
    GoModule,
    ImportSpec,
    IndexResult,
    PackageInfo,
    Stats,
    SymbolRecord,
    content_hash,
    package_order_key,
)

SYMBOL_DEFAULTS: dict[str, object] = {
    "id": "func:p:F",
    "kind": KIND_FUNC,
    "package": "p",
    "package_path": "example.com/p",
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


def make_package(path: str = "example.com/p", name: str = "p", **overrides: object) -> PackageInfo:
    """A :class:`PackageInfo` with a sane directory and key."""
    defaults: dict[str, object] = {
        "path": path,
        "name": name,
        "directory": path,
    }
    return PackageInfo(**{**defaults, **overrides})  # type: ignore[arg-type]


class ContentHashTest(unittest.TestCase):
    def test_is_stable_and_content_specific(self) -> None:
        first = content_hash(b"package p\n")
        self.assertEqual(first, content_hash(b"package p\n"))
        self.assertNotEqual(first, content_hash(b"package q\n"))
        self.assertEqual(len(first), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in first))

    def test_the_empty_file_has_a_hash_too(self) -> None:
        self.assertEqual(len(content_hash(b"")), 64)


class ImportSpecTest(unittest.TestCase):
    def test_local_name_prefers_the_alias(self) -> None:
        self.assertEqual(ImportSpec(path="example.com/a/b", alias="b_alias").local_name, "b_alias")

    def test_local_name_is_the_last_path_segment(self) -> None:
        self.assertEqual(ImportSpec(path="example.com/a/b").local_name, "b")
        self.assertEqual(ImportSpec(path="fmt").local_name, "fmt")

    def test_dot_and_blank_imports_keep_their_alias(self) -> None:
        self.assertEqual(ImportSpec(path="example.com/a", alias=".").local_name, ".")
        self.assertEqual(ImportSpec(path="example.com/a", alias="_").local_name, "_")

    def test_to_dict_key_order(self) -> None:
        spec = ImportSpec(path="fmt", alias="f", line=3)
        self.assertEqual(list(spec.to_dict()), ["path", "alias", "line"])
        self.assertEqual(spec.to_dict(), {"path": "fmt", "alias": "f", "line": 3})


class SymbolRecordTest(unittest.TestCase):
    #: The order documented for ``symbols.jsonl``.
    KEYS = (
        "id",
        "kind",
        "package",
        "package_path",
        "file",
        "name",
        "qualified_name",
        "receiver",
        "receiver_type",
        "signature",
        "doc",
        "exported",
        "start_line",
        "end_line",
        "start_byte",
        "end_byte",
        "calls",
        "callers",
        "types",
        "fields",
        "methods",
        "imports",
        "test_related",
        "content_hash",
        "unresolved_calls",
    )

    def test_to_dict_uses_the_documented_key_order(self) -> None:
        self.assertEqual(list(make_symbol().to_dict()), list(self.KEYS))

    def test_list_fields_default_to_empty_lists(self) -> None:
        record = make_symbol().to_dict()
        for key in ("calls", "callers", "types", "fields", "methods", "imports", "unresolved_calls"):
            self.assertEqual(record[key], [])

    def test_list_fields_are_not_shared_between_records(self) -> None:
        first, second = make_symbol(), make_symbol()
        first.calls.append("x")
        self.assertEqual(second.calls, [])

    def test_is_package_only_for_package_symbols(self) -> None:
        self.assertTrue(make_symbol(kind=KIND_PACKAGE).is_package)
        self.assertFalse(make_symbol().is_package)

    def test_to_json_is_a_single_line(self) -> None:
        text = make_symbol(doc="first\nsecond").to_json()
        self.assertNotIn("\n", text)
        self.assertEqual(json.loads(text)["doc"], "first\nsecond")


class FileRecordTest(unittest.TestCase):
    KEYS = (
        "file",
        "package",
        "package_path",
        "lines",
        "symbols",
        "imports",
        "build_tags",
        "test_related",
        "parse_error",
        "error",
        "content_hash",
    )

    def test_to_dict_uses_the_documented_key_order(self) -> None:
        record = FileRecord(file="p/p.go", package="p", package_path="example.com/p", lines=10, symbols=2)
        self.assertEqual(list(record.to_dict()), list(self.KEYS))
        self.assertEqual(record.to_dict()["lines"], 10)

    def test_flags_and_lists_default_to_false_and_empty(self) -> None:
        record = FileRecord(file="p/p.go", package="p", package_path="example.com/p", lines=0, symbols=0)
        self.assertEqual((record.test_related, record.parse_error, record.error), (False, False, ""))
        self.assertEqual((record.imports, record.build_tags, record.content_hash), ([], [], ""))

    def test_to_json_is_a_single_line(self) -> None:
        record = FileRecord(
            file="p/p.go",
            package="p",
            package_path="example.com/p",
            lines=10,
            symbols=2,
            error="p/p.go:3: unexpected }",
        )
        self.assertNotIn("\n", record.to_json())


class PackageInfoTest(unittest.TestCase):
    def test_key_combines_path_and_name(self) -> None:
        self.assertEqual(make_package().key, "example.com/p|p")

    def test_the_external_test_package_gets_its_own_key(self) -> None:
        self.assertNotEqual(
            make_package(name="p").key, make_package(name="p_test").key
        )

    def test_is_test_package(self) -> None:
        self.assertFalse(make_package().is_test_package)
        self.assertTrue(make_package(name="p_test").is_test_package)
        self.assertTrue(make_package(test_related=True).is_test_package)

    def test_symbols_of_kinds(self) -> None:
        struct = make_symbol(id="struct:p:T", kind=KIND_STRUCT, name="T")
        method = make_symbol(id="method:p:T.M", kind=KIND_METHOD, name="M")
        package = make_package(symbols=[struct, method])
        self.assertEqual(package.symbols_of_kinds([KIND_STRUCT]), [struct])
        self.assertEqual(package.symbols_of_kinds([KIND_METHOD, KIND_FUNC]), [method])
        self.assertEqual(package.symbols_of_kinds([]), [])


class SymbolOrderingTest(unittest.TestCase):
    def test_exported_symbols_come_first_then_kind_order(self) -> None:
        package = make_package(
            symbols=[
                make_symbol(id="var:p:z", name="z", exported=True),
                make_symbol(id="func:p:B", kind=KIND_FUNC, name="B", exported=True),
                make_symbol(id="struct:p:T", kind=KIND_STRUCT, name="T", exported=True),
                make_symbol(id="func:p:a", kind=KIND_FUNC, name="a", exported=False),
            ]
        )
        self.assertEqual(
            [symbol.id for symbol in package.sorted_symbols()],
            ["struct:p:T", "func:p:B", "var:p:z", "func:p:a"],
        )

    def test_names_are_compared_case_insensitively(self) -> None:
        package = make_package(
            symbols=[
                make_symbol(id="func:p:Zebra", name="Zebra"),
                make_symbol(id="func:p:apple", name="apple"),
                make_symbol(id="func:p:Beta", name="Beta"),
            ]
        )
        self.assertEqual(
            [symbol.name for symbol in package.sorted_symbols()],
            ["apple", "Beta", "Zebra"],
        )

    def test_same_named_symbols_are_ordered_by_line(self) -> None:
        package = make_package(
            symbols=[
                make_symbol(id="func:p:F#b", name="F", start_line=9),
                make_symbol(id="func:p:F", name="F", start_line=3),
            ]
        )
        self.assertEqual(
            [symbol.start_line for symbol in package.sorted_symbols()], [3, 9]
        )


class PackageOrderKeyTest(unittest.TestCase):
    def paths(self, *paths: str) -> list[str]:
        packages = [
            make_package(path=path, name=path.rsplit("/", 1)[-1]) for path in paths
        ]
        return [package.path for package in sorted(packages, key=package_order_key)]

    def test_a_package_comes_before_its_subpackages(self) -> None:
        self.assertEqual(self.paths("a/b/c", "a", "a/b"), ["a", "a/b", "a/b/c"])

    def test_segments_beat_plain_string_order(self) -> None:
        # "-" sorts before "/" as a string, so ``sorted(paths)`` would put ``a-b``
        # first and break the "package above its subpackages" reading.
        self.assertEqual(sorted(["a-b", "a/b"]), ["a-b", "a/b"])
        self.assertEqual(self.paths("a-b", "a/b"), ["a/b", "a-b"])

    def test_the_same_path_is_ordered_by_package_name(self) -> None:
        packages = [make_package(name="p_test"), make_package(name="p")]
        self.assertEqual(
            [package.name for package in sorted(packages, key=package_order_key)],
            ["p", "p_test"],
        )

    def test_the_key_is_a_plain_tuple(self) -> None:
        key = package_order_key(make_package(path="a/b", name="b"))
        self.assertEqual(key, (("a", "b"), "b"))


class StatsTest(unittest.TestCase):
    KEYS = (
        "generated_at",
        "root",
        "module_path",
        "go_version",
        "packages",
        "files",
        "symbols",
        "calls",
        "resolved_calls",
        "unresolved_calls",
        "parse_errors",
        "duration_seconds",
        "backend",
        "ignored_files",
    )

    def stats(self, **overrides: object) -> Stats:
        defaults: dict[str, object] = {
            "generated_at": "2024-01-01T00:00:00Z",
            "root": ".",
        }
        return Stats(**{**defaults, **overrides})  # type: ignore[arg-type]

    def test_to_dict_uses_the_documented_key_order(self) -> None:
        self.assertEqual(list(self.stats().to_dict()), list(self.KEYS))

    def test_duration_is_rounded_to_milliseconds(self) -> None:
        self.assertEqual(
            self.stats(duration_seconds=1.23456).to_dict()["duration_seconds"], 1.235
        )
        self.assertEqual(self.stats(duration_seconds=2).to_dict()["duration_seconds"], 2.0)

    def test_summary_lines(self) -> None:
        stats = self.stats(
            packages=2,
            files=3,
            symbols=4,
            calls=5,
            resolved_calls=3,
            unresolved_calls=2,
            parse_errors=1,
            duration_seconds=1.5,
        )
        self.assertEqual(
            stats.summary_lines(),
            [
                "packages: 2",
                "files: 3",
                "symbols: 4",
                "calls: 5 (resolved 3, unresolved 2)",
                "parse errors: 1",
                "duration: 1.50s",
            ],
        )


def make_result(*packages: PackageInfo) -> IndexResult:
    """An :class:`IndexResult` holding ``packages`` and otherwise empty fields."""
    return IndexResult(
        root="demo",
        module=GoModule(
            path="demo/go.mod", module_path="example.com/demo", go_version="1.22"
        ),
        packages={package.key: package for package in packages},
        symbols=[],
        files=[],
        stats=Stats(generated_at="2024-01-01T00:00:00Z", root="demo"),
    )


class IndexResultTest(unittest.TestCase):
    def test_iter_packages_is_deterministic(self) -> None:
        result = make_result(
            make_package(path="demo/b", name="b"),
            make_package(path="demo", name="demo"),
            make_package(path="demo/b/c", name="c"),
        )
        self.assertEqual(
            [package.path for package in result.iter_packages()],
            ["demo", "demo/b", "demo/b/c"],
        )

    def test_production_packages_skip_the_test_packages(self) -> None:
        result = make_result(
            make_package(path="demo", name="demo"),
            make_package(path="demo/api", name="api"),
            make_package(path="demo/api", name="api_test"),
        )
        self.assertEqual(
            [package.name for package in result.production_packages()], ["demo", "api"]
        )

    def test_to_dict_summarises_the_run(self) -> None:
        package = make_package(symbols=[make_symbol()], files=["demo/demo.go"])
        payload = make_result(package).to_dict()
        self.assertEqual(list(payload), ["root", "module", "packages", "stats"])
        self.assertEqual(list(payload["module"]), ["path", "module_path", "go_version"])
        self.assertEqual(
            payload["packages"],
            [
                {
                    "key": "example.com/p|p",
                    "path": "example.com/p",
                    "name": "p",
                    "directory": "example.com/p",
                    "files": 1,
                    "symbols": 1,
                    "test_package": False,
                }
            ],
        )
        self.assertEqual(payload["stats"]["files"], 0)

    def test_to_json_is_a_single_line_and_keeps_non_ascii(self) -> None:
        text = make_result(make_package(path="café", name="café")).to_json()
        self.assertNotIn("\n", text)
        self.assertIn("café", text)


if __name__ == "__main__":
    unittest.main()
