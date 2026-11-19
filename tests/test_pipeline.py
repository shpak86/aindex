"""The pipeline: discovery + parsing + linking, then writing the index."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from aindex import pipeline
from aindex.constants import DEFAULT_OUTPUT_DIR
from aindex.pipeline import (
    BACKEND_PYTHON,
    MISSING_PACKAGE_ERROR,
    IndexOptions,
    build_index,
    run,
    timestamp,
    write_index,
)
from aindex.render import package_pages
from tests.support import (
    IGNORED_FILES,
    INDEXED_FILES,
    MODULE_PATH,
    IndexTestCase,
    index_dir_for,
    read_json,
    read_jsonl,
    read_text,
)


class IndexOptionsTest(unittest.TestCase):
    def test_root_is_absolute(self) -> None:
        self.assertEqual(IndexOptions(root=".").resolved_root(), os.getcwd())

    def test_default_output_directory_sits_in_the_project_root(self) -> None:
        options = IndexOptions(root="/tmp/project")
        self.assertEqual(options.output_dir, DEFAULT_OUTPUT_DIR)
        self.assertEqual(options.resolved_output_dir(), "/tmp/project/.ai-index")

    def test_relative_output_directory_is_resolved_against_the_root(self) -> None:
        options = IndexOptions(root="/tmp/project", output_dir="out/index")
        self.assertEqual(options.resolved_output_dir(), "/tmp/project/out/index")

    def test_absolute_output_directory_is_kept(self) -> None:
        options = IndexOptions(root="/tmp/project", output_dir="/var/tmp/ai")
        self.assertEqual(options.resolved_output_dir(), "/var/tmp/ai")

    def test_empty_output_directory_falls_back_to_the_default(self) -> None:
        options = IndexOptions(root="/tmp/project", output_dir="")
        self.assertEqual(options.resolved_output_dir(), "/tmp/project/.ai-index")

    def test_paths_are_normalised(self) -> None:
        options = IndexOptions(root="/tmp/project/..", output_dir="./out/")
        self.assertEqual(options.resolved_root(), "/tmp")
        self.assertEqual(options.resolved_output_dir(), "/tmp/out")

    def test_defaults_index_tests_and_respect_gitignore(self) -> None:
        options = IndexOptions()
        self.assertTrue(options.include_tests)
        self.assertTrue(options.use_gitignore)
        self.assertFalse(options.clean)
        self.assertEqual(options.extra_ignores, ())


class TimestampTest(unittest.TestCase):
    def test_source_date_epoch_is_honoured(self) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            self.assertEqual(timestamp(), "2023-11-14T22:13:20Z")

    def test_format_is_iso_8601_utc_without_microseconds(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertRegex(timestamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_a_non_numeric_epoch_falls_back_to_the_clock(self) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "yesterday"}):
            self.assertRegex(timestamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class BuildIndexTest(IndexTestCase):
    """``build_index`` on the demo project."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()

    def test_module_metadata_comes_from_go_mod(self) -> None:
        module = self.result.module
        self.assertEqual(module.module_path, MODULE_PATH)
        self.assertEqual(module.go_version, "1.21.5")
        self.assertEqual(module.path, os.path.join(self.root, "go.mod"))

    def test_only_discovered_go_files_are_indexed(self) -> None:
        self.assertEqual(
            [record.file for record in self.result.files], list(INDEXED_FILES)
        )

    def test_ignored_files_are_counted_but_not_indexed(self) -> None:
        indexed = {record.file for record in self.result.files}
        for ignored in IGNORED_FILES:
            self.assertNotIn(ignored, indexed)
        self.assertEqual(self.result.stats.ignored_files, 1)

    def test_packages_are_keyed_by_path_and_name(self) -> None:
        self.assertEqual(
            sorted(self.result.packages),
            [
                "example.com/demo/broken|broken",
                "example.com/demo/internal/api_test|api_test",
                "example.com/demo/internal/api|api",
                "example.com/demo/internal/store|store",
                "example.com/demo/platform|platform",
                "example.com/demo|main",
            ],
        )

    def test_package_metadata(self) -> None:
        store = self.result.packages["example.com/demo/internal/store|store"]
        self.assertEqual(store.path, "example.com/demo/internal/store")
        self.assertEqual(store.directory, "internal/store")
        self.assertEqual(store.doc, "Package store keeps items in memory.")
        self.assertEqual(
            store.files, ["internal/store/store.go", "internal/store/store_test.go"]
        )
        self.assertFalse(store.is_test_package)

    def test_root_package_has_an_empty_directory(self) -> None:
        root = self.result.packages["example.com/demo|main"]
        self.assertEqual(root.directory, "")
        self.assertEqual(root.doc, "Command demo stores and reads one answer.")
        self.assertEqual(root.files, ["keep.gen.go", "main.go"])

    def test_external_test_package_is_marked(self) -> None:
        external = self.result.packages["example.com/demo/internal/api_test|api_test"]
        self.assertEqual(external.path, "example.com/demo/internal/api_test")
        self.assertTrue(external.is_test_package)
        self.assertTrue(external.test_related)

    def test_files_without_a_package_doc_keep_an_empty_doc(self) -> None:
        broken = self.result.packages["example.com/demo/broken|broken"]
        self.assertEqual(broken.doc, "")
        self.assertEqual(self.result.packages["example.com/demo/platform|platform"].doc, "")

    def test_stats_describe_the_run(self) -> None:
        stats = self.result.stats
        self.assertEqual(stats.root, self.root)
        self.assertEqual(stats.module_path, MODULE_PATH)
        self.assertEqual(stats.go_version, "1.21.5")
        self.assertEqual(stats.packages, 6)
        self.assertEqual(stats.files, 10)
        self.assertEqual(stats.symbols, 28)
        self.assertEqual(stats.parse_errors, 2)
        self.assertEqual(stats.backend, BACKEND_PYTHON)
        self.assertEqual(stats.ignored_files, 1)
        self.assertGreaterEqual(stats.duration_seconds, 0.0)
        self.assertEqual(stats.calls, stats.resolved_calls + stats.unresolved_calls)
        self.assertEqual((stats.resolved_calls, stats.unresolved_calls), (6, 15))
        self.assertRegex(stats.generated_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_symbol_ids_are_unique(self) -> None:
        ids = [record.id for record in self.result.symbols]
        self.assertEqual(len(ids), len(set(ids)))

    def test_each_package_contributes_one_package_symbol(self) -> None:
        packages = {
            record.name: record
            for record in self.result.symbols
            if record.kind == "package"
        }
        self.assertEqual(
            sorted(packages), sorted(p.name for p in self.result.packages.values())
        )
        for package in self.result.packages.values():
            record = packages[package.name]
            self.assertEqual(record.id, f"package:{package.path}:{package.name}")
            self.assertEqual(record.package, package.name)
            self.assertEqual(record.package_path, package.path)
            self.assertEqual(record.signature, f"package {package.name}")
            self.assertEqual(record.doc, package.doc)
            self.assertEqual(record.file, "")
            self.assertEqual(
                record.imports,
                sorted({path for symbol in package.symbols for path in symbol.imports}),
            )

    def test_package_symbols_come_first_in_the_record_list(self) -> None:
        kinds = [record.kind for record in self.result.symbols]
        # One package record plus 28 declarations; the package record leads each group.
        self.assertEqual(len(kinds), 34)
        positions = [index for index, kind in enumerate(kinds) if kind == "package"]
        self.assertEqual(positions, [0, 4, 7, 14, 16, 31])

    def test_file_records_carry_metadata(self) -> None:
        records = {record.file: record for record in self.result.files}
        store = records["internal/store/store.go"]
        self.assertEqual(store.package, "store")
        self.assertEqual(store.package_path, "example.com/demo/internal/store")
        self.assertEqual(store.lines, 70)
        self.assertEqual(store.imports, ["context", "fmt", "sync"])
        self.assertEqual(store.build_tags, [])
        self.assertEqual(len(store.content_hash), 64)
        self.assertFalse(store.parse_error)
        self.assertFalse(store.test_related)

    def test_build_tags_reach_the_file_record(self) -> None:
        records = {record.file: record for record in self.result.files}
        self.assertEqual(records["platform/mode_linux.go"].build_tags, ["go:build linux"])
        self.assertEqual(
            records["platform/mode_windows.go"].build_tags, ["go:build windows"]
        )

    def test_broken_files_are_reported_but_kept(self) -> None:
        records = {record.file: record for record in self.result.files}
        broken = records["broken/broken.go"]
        self.assertTrue(broken.parse_error)
        self.assertEqual(broken.error, "line 4: unterminated string or rune literal")
        self.assertEqual(broken.package, "broken")
        self.assertEqual(broken.symbols, 2)
        self.assertIn(
            "func:example.com/demo/broken:StillParsed", self.by_id(self.result)
        )

    def test_files_without_a_package_clause_are_not_indexed(self) -> None:
        records = {record.file: record for record in self.result.files}
        orphan = records["orphan/nopackage.go"]
        self.assertTrue(orphan.parse_error)
        self.assertEqual(orphan.error, MISSING_PACKAGE_ERROR)
        self.assertEqual(orphan.package, "")
        self.assertEqual(orphan.symbols, 0)
        self.assertEqual(orphan.lines, 3)
        self.assertNotIn("example.com/demo/orphan", self.result.packages)

    def test_test_files_are_marked(self) -> None:
        records = {record.file: record for record in self.result.files}
        self.assertTrue(records["internal/store/store_test.go"].test_related)
        self.assertTrue(records["internal/api/api_test.go"].test_related)
        self.assertFalse(records["internal/store/store.go"].test_related)

    def test_methods_carry_their_receiver_type(self) -> None:
        get = self.symbol("method:example.com/demo/internal/store:memory.Get")
        self.assertEqual(get.receiver, "*memory")
        self.assertEqual(get.receiver_type, "memory")
        self.assertEqual(get.name, "Get")
        self.assertEqual(get.qualified_name, "memory.Get")

    def test_files_declaring_the_same_symbol_get_suffixed_ids(self) -> None:
        ids = {record.id for record in self.result.symbols}
        linux = "const:example.com/demo/platform:Mode#platform/mode_linux.go"
        windows = "const:example.com/demo/platform:Mode#platform/mode_windows.go"
        self.assertIn(linux, ids)
        self.assertIn(windows, ids)
        self.assertNotIn("const:example.com/demo/platform:Mode", ids)
        self.assertEqual(self.by_id(self.result)[linux].file, "platform/mode_linux.go")

    def test_file_records_count_the_symbols_they_contain(self) -> None:
        per_file = {record.file: record.symbols for record in self.result.files}
        self.assertEqual(per_file["internal/store/store.go"], 13)
        self.assertEqual(per_file["main.go"], 2)
        self.assertEqual(per_file["broken/broken.go"], 2)
        self.assertEqual(per_file["orphan/nopackage.go"], 0)
        self.assertEqual(sum(per_file.values()), self.result.stats.symbols)

    def test_doc_truncation_limit_is_configurable(self) -> None:
        result = self.build(max_doc_chars=10)
        records = self.by_id(result)
        item = records["struct:example.com/demo/internal/store:Item"]
        self.assertEqual(item.doc, "Item is...")
        package = records["package:example.com/demo/internal/store:store"]
        self.assertEqual(package.doc, "Package...")

    def test_doc_truncation_off_keeps_the_whole_comment(self) -> None:
        result = self.build(max_doc_chars=0)
        item = self.by_id(result)["struct:example.com/demo/internal/store:Item"]
        self.assertEqual(item.doc, "Item is one stored value.")


class OptionVariationTest(IndexTestCase):
    """The switches that change what gets indexed."""

    def test_tests_can_be_excluded(self) -> None:
        result = self.build(include_tests=False)
        self.assertEqual([record.file for record in result.files], [
            "broken/broken.go",
            "internal/api/api.go",
            "internal/store/store.go",
            "keep.gen.go",
            "main.go",
            "orphan/nopackage.go",
            "platform/mode_linux.go",
            "platform/mode_windows.go",
        ])
        self.assertEqual(result.stats.files, 8)
        self.assertEqual(result.stats.packages, 5)
        self.assertEqual(result.stats.symbols, 26)
        self.assertEqual(result.stats.parse_errors, 2)
        self.assertEqual(result.stats.ignored_files, 1)
        self.assertNotIn("example.com/demo/internal/api_test|api_test", result.packages)
        self.assertFalse(any(record.test_related for record in result.files))
        self.assertFalse(any(record.kind == "test" for record in result.symbols))

    def test_excluding_tests_also_changes_the_call_counts(self) -> None:
        result = self.build(include_tests=False)
        self.assertEqual(
            (result.stats.resolved_calls, result.stats.unresolved_calls), (4, 8)
        )

    def test_extra_ignore_patterns_drop_directories(self) -> None:
        result = self.build(extra_ignores=("platform/",))
        self.assertEqual(result.stats.files, 8)
        self.assertEqual(result.stats.packages, 5)
        self.assertEqual(result.stats.symbols, 26)
        self.assertNotIn("example.com/demo/platform|platform", result.packages)
        indexed = {record.file for record in result.files}
        self.assertFalse(any(path.startswith("platform/") for path in indexed))

    def test_extra_ignore_patterns_win_over_the_defaults(self) -> None:
        result = self.build(extra_ignores=("*.go",))
        self.assertEqual(result.files, [])

    def test_ignoring_more_files_does_not_change_the_gitignore_count(self) -> None:
        self.assertEqual(self.build(extra_ignores=("platform/",)).stats.ignored_files, 1)

    def test_the_gitignore_can_be_disabled(self) -> None:
        result = self.build(use_gitignore=False)
        self.assertEqual(result.stats.files, 12)
        self.assertEqual(result.stats.packages, 8)
        self.assertEqual(result.stats.symbols, 30)
        self.assertEqual(result.stats.ignored_files, 0)
        indexed = {record.file for record in result.files}
        self.assertIn("gen/db.gen.go", indexed)
        self.assertIn("tmp/cache.go", indexed)
        self.assertIn("example.com/demo/gen|gen", result.packages)
        self.assertIn("example.com/demo/tmp|tmp", result.packages)

    def test_the_switches_compose(self) -> None:
        result = self.build(use_gitignore=False, include_tests=False)
        self.assertEqual(result.stats.files, 10)
        self.assertEqual(result.stats.packages, 7)
        self.assertEqual(result.stats.symbols, 28)
        self.assertEqual(result.stats.ignored_files, 0)

    def test_generated_files_are_indexed_unless_ignored(self) -> None:
        result = self.build(use_gitignore=False)
        generated = self.by_id(result)["func:example.com/demo/gen:DB"]
        self.assertEqual(generated.file, "gen/db.gen.go")
        self.assertEqual(generated.doc, "DB is generated.")
        self.assertTrue(generated.exported)
        keep = self.symbol("func:example.com/demo:generated", self.build())
        self.assertEqual(keep.file, "keep.gen.go")


class WriteIndexTest(IndexTestCase):
    """``run`` / ``write_index``: the files on disk."""

    def test_run_writes_every_file_in_a_stable_order(self) -> None:
        _, written = self.write_index()
        self.assertEqual(
            [os.path.relpath(path, self.root) for path in written],
            [
                ".ai-index/symbols.jsonl",
                ".ai-index/files.jsonl",
                ".ai-index/stats.json",
                ".ai-index/PROJECT.md",
                ".ai-index/packages/root.md",
                ".ai-index/packages/broken.md",
                ".ai-index/packages/internal_api.md",
                ".ai-index/packages/internal_api-test.md",
                ".ai-index/packages/internal_store.md",
                ".ai-index/packages/platform.md",
            ],
        )
        for path in written:
            self.assertTrue(os.path.isfile(path), path)

    def test_run_returns_the_result_it_wrote(self) -> None:
        result, _ = self.write_index()
        self.assertEqual(result.root, self.root)
        self.assertEqual(result.stats.files, 10)
        self.assertEqual(len(result.symbols), 34)

    def test_symbols_file_matches_the_result(self) -> None:
        result, _ = self.write_index()
        records = read_jsonl(os.path.join(self.index_dir, "symbols.jsonl"))
        self.assertEqual(records, [record.to_dict() for record in result.symbols])

    def test_files_file_matches_the_result(self) -> None:
        result, _ = self.write_index()
        records = read_jsonl(os.path.join(self.index_dir, "files.jsonl"))
        self.assertEqual(records, [record.to_dict() for record in result.files])

    def test_stats_file_matches_the_result(self) -> None:
        result, _ = self.write_index()
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual(stats, result.stats.to_dict())
        self.assertEqual(stats["backend"], BACKEND_PYTHON)
        self.assertEqual(stats["files"], 10)

    def test_the_output_directory_holds_nothing_else(self) -> None:
        self.write_index()
        self.assertEqual(
            sorted(os.listdir(self.index_dir)),
            ["PROJECT.md", "files.jsonl", "packages", "stats.json", "symbols.jsonl"],
        )
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.index_dir, "packages"))),
            [
                "broken.md",
                "internal_api-test.md",
                "internal_api.md",
                "internal_store.md",
                "platform.md",
                "root.md",
            ],
        )

    def test_generated_at_honours_source_date_epoch(self) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            result, _ = self.write_index()
        self.assertEqual(result.stats.generated_at, "2023-11-14T22:13:20Z")
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual(stats["generated_at"], "2023-11-14T22:13:20Z")

    def test_the_index_is_not_indexed_itself(self) -> None:
        self.write_index()
        self.assertEqual(self.build().stats.files, 10)
        self.assertEqual(
            self.by_id(self.result)["func:example.com/demo:main"].file, "main.go"
        )

    def test_a_relative_output_directory_is_written_below_the_root(self) -> None:
        _, written = self.write_index(output_dir="build/ai")
        self.assertEqual(
            os.path.dirname(written[0]), os.path.join(self.root, "build", "ai")
        )
        self.assertTrue(os.path.isfile(os.path.join(self.root, "build/ai/PROJECT.md")))
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.root, "build", "ai"))),
            ["PROJECT.md", "files.jsonl", "packages", "stats.json", "symbols.jsonl"],
        )

    def test_clean_removes_previous_output(self) -> None:
        self.write_index()
        stale = os.path.join(self.index_dir, "stale.md")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("old\n")
        written = self.write_index(clean=True)[1]
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(all(os.path.isfile(path) for path in written))

    def test_a_stale_file_survives_without_clean(self) -> None:
        self.write_index()
        stale = os.path.join(self.index_dir, "stale.md")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("old\n")
        self.write_index()
        self.assertTrue(os.path.exists(stale))

    def test_run_refuses_the_project_root_as_output_directory(self) -> None:
        with self.assertRaises(ValueError):
            run(IndexOptions(root=self.root, output_dir="."))

    def test_run_refuses_a_parent_of_the_project_root(self) -> None:
        with self.assertRaises(ValueError):
            run(IndexOptions(root=self.root, output_dir=".."))

    def test_write_index_works_without_running_the_pipeline(self) -> None:
        result = self.build()
        written = write_index(result, self.index_dir)
        self.assertEqual(written[0], os.path.join(self.index_dir, "symbols.jsonl"))
        self.assertTrue(all(os.path.isfile(path) for path in written))


class IdempotencyTest(IndexTestCase):
    """Two runs over an unchanged tree must produce the same bytes."""

    def test_two_runs_are_byte_identical(self) -> None:
        clock = [0.0, 1.5, 0.0, 1.5]
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            with mock.patch.object(
                pipeline.time, "perf_counter", side_effect=lambda: clock.pop(0)
            ):
                _, first = self.write_index()
                snapshot = {path: read_text(path) for path in first}
                _, second = self.write_index()
        self.assertEqual(first, second)
        self.assertEqual(snapshot, {path: read_text(path) for path in second})

    def test_written_files_are_utf8_with_lf_endings(self) -> None:
        _, written = self.write_index()
        for path in written:
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"\r\n", raw, path)
            raw.decode("utf-8")

    def test_markdown_pages_end_with_one_newline(self) -> None:
        _, written = self.write_index()
        for path in written:
            if path.endswith(".md"):
                text = read_text(path)
                self.assertTrue(text.endswith("\n"), path)
                self.assertFalse(text.endswith("\n\n"), path)

    def test_rewriting_the_same_result_is_idempotent(self) -> None:
        """Every file except ``stats.json`` is a pure function of the result."""
        result = self.build()
        first = write_index(result, self.index_dir)
        snapshot = {path: read_text(path) for path in first}
        second = write_index(self.build(), self.index_dir)
        self.assertEqual(first, second)
        for path in first:
            if os.path.basename(path) == "stats.json":
                continue  # duration_seconds differs between runs
            self.assertEqual(snapshot[path], read_text(path), path)


if __name__ == "__main__":
    unittest.main()

