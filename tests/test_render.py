"""Markdown rendering: PROJECT.md, the package pages and the small helpers."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import aindex.render as render
from aindex.model import FileRecord, PackageInfo
from aindex.render import (
    _cell,
    _code_block,
    _doc_block,
    _label,
    _plural,
    _shorten,
    _slug,
    package_pages,
    render_package,
    render_project,
    write_markdown,
)
from aindex.constants import KIND_INTERFACE, KIND_METHOD, KIND_STRUCT
from tests.support import IndexTestCase, read_text


class HelperTest(unittest.TestCase):
    def test_slug_of_the_root_directory(self) -> None:
        self.assertEqual(_slug("", "main"), "root")

    def test_slug_flattens_directories(self) -> None:
        self.assertEqual(_slug("internal/store", "store"), "internal_store")

    def test_slug_marks_test_packages(self) -> None:
        self.assertEqual(_slug("internal/api", "api_test"), "internal_api-test")

    def test_slug_replaces_awkward_characters(self) -> None:
        # Directory separators become ``_``; anything else odd becomes ``-``.
        self.assertEqual(_slug("a b/c+d", "p"), "a-b_c-d")

    def test_shorten_keeps_names_of_the_current_package(self) -> None:
        self.assertEqual(_shorten("struct:pkg/p:Item", "pkg/p"), "Item")

    def test_shorten_keeps_full_ids_of_other_packages(self) -> None:
        target = "struct:other/pkg:Item"
        self.assertEqual(_shorten(target, "pkg/p"), target)

    def test_cell_escapes_pipes_and_newlines(self) -> None:
        self.assertEqual(_cell("a | b\nc"), "a \\| b c")

    def test_doc_block_quotes_every_line(self) -> None:
        self.assertEqual(_doc_block("one\ntwo"), ["> one", "> two", ""])
        self.assertEqual(_doc_block(""), [])

    def test_code_block_is_fenced_and_trimmed(self) -> None:
        self.assertEqual(
            _code_block("func f() {}\n\n"), ["```go", "func f() {}", "```", ""]
        )
        self.assertEqual(_code_block(""), [])

    def test_labels_and_titles(self) -> None:
        self.assertEqual(_label(KIND_METHOD), "method")
        self.assertEqual(_label("unknown"), "unknown")
        self.assertEqual(_plural(KIND_STRUCT), "Structs")
        self.assertEqual(_plural("unknown"), "Unknowns")


class PackagePagesTest(unittest.TestCase):
    def test_every_package_gets_one_page(self) -> None:
        pages = package_pages(
            [
                PackageInfo(path="example.com/demo", name="main", directory=""),
                PackageInfo(
                    path="example.com/demo/internal/store",
                    name="store",
                    directory="internal/store",
                ),
            ]
        )
        self.assertEqual(
            pages,
            {
                "example.com/demo|main": "packages/root.md",
                "example.com/demo/internal/store|store": "packages/internal_store.md",
            },
        )

    def test_directories_that_slug_to_the_same_name_get_a_suffix(self) -> None:
        pages = package_pages(
            [
                PackageInfo(path="a/b", name="p", directory="a/b"),
                PackageInfo(path="a_b", name="q", directory="a_b"),
            ]
        )
        self.assertEqual(
            sorted(pages.values()), ["packages/a_b-2.md", "packages/a_b.md"]
        )


class ProjectRenderTest(IndexTestCase):
    """``PROJECT.md``: the orientation page."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()
        self.pages = package_pages(self.result.packages.values())
        self.text = render_project(self.result, self.pages)

    def test_title_is_the_module_path(self) -> None:
        self.assertTrue(self.text.startswith("# example.com/demo — Go project index\n"))

    def test_facts_line_lists_root_generation_time_and_backend(self) -> None:
        facts = self.text.splitlines()[2]
        self.assertIn(f"root `{self.root}`", facts)
        self.assertIn(f"generated {self.result.stats.generated_at}", facts)
        self.assertIn("go 1.21.5", facts)
        self.assertIn(f"backend `{self.result.stats.backend}`", facts)

    def test_summary_counts_come_from_the_stats(self) -> None:
        stats = self.result.stats
        self.assertIn("## Summary", self.text)
        self.assertIn("| Packages | 6 (1 test) |", self.text)
        self.assertIn(f"| Go files | {stats.files} |", self.text)
        self.assertIn(f"| Symbols | {stats.symbols} |", self.text)
        self.assertIn(
            f"| Calls | {stats.calls} ({stats.resolved_calls} resolved, "
            f"{stats.unresolved_calls} unresolved) |",
            self.text,
        )
        self.assertIn("| Parse errors | 2 |", self.text)
        self.assertIn("| Ignored files | 1 |", self.text)

    def test_index_files_section_describes_the_layout(self) -> None:
        self.assertIn("## Index files", self.text)
        for name in ("`PROJECT.md`", "`symbols.jsonl`", "`files.jsonl`", "`stats.json`"):
            self.assertIn(name, self.text)
        # The example query is scoped to this module when one is known.
        self.assertIn("grep 'func:example.com/demo/' symbols.jsonl", self.text)

    def test_package_table_links_every_package_page(self) -> None:
        self.assertIn("## Packages", self.text)
        self.assertIn(
            "| `main` | `.` | 2 | 3 | [root.md](packages/root.md) |", self.text
        )
        self.assertIn(
            "| `api_test` *(test)* | `internal/api` | 1 | 1 | "
            "[internal_api-test.md](packages/internal_api-test.md) |",
            self.text,
        )
        for package in self.result.iter_packages():
            self.assertIn(f"]({self.pages[package.key]})", self.text)

    def test_entry_points_list_exported_functions_only(self) -> None:
        self.assertIn("## Entry points (exported package-level functions)", self.text)
        self.assertIn(
            "| `NewHandler` | [`api`](packages/internal_api.md) | "
            "`func NewHandler(s store.Store) *Handler` |",
            self.text,
        )
        table = self.text.split("## Entry points", 1)[1].split("## Exported types", 1)[0]
        self.assertNotIn("`run`", table)
        self.assertNotIn("`generated`", table)
        self.assertNotIn("`TestHandlerGet`", table)

    def test_exported_types_table(self) -> None:
        self.assertIn("## Exported types", self.text)
        self.assertIn(
            "| `Store` | [`store`](packages/internal_store.md) | `type Store interface` |",
            self.text,
        )

    def test_entry_point_tables_are_truncated(self) -> None:
        with mock.patch.object(render, "MAX_PROJECT_ENTRIES", 1):
            text = render_project(self.result, self.pages)
        self.assertIn("| *… 3 more* |  | see the package pages |", text)

    def test_parse_errors_are_reported(self) -> None:
        section = self.text.split("## Parse errors", 1)[1].split("## Notes", 1)[0]
        self.assertIn("- `broken/broken.go`: line 4: unterminated string or rune literal", section)
        self.assertIn("- `orphan/nopackage.go`: missing package clause", section)

    def test_parse_error_section_is_omitted_when_everything_parses(self) -> None:
        self.result.files = [record for record in self.result.files if not record.parse_error]
        self.assertNotIn("## Parse errors", render_project(self.result, self.pages))

    def test_notes_explain_resolution_and_exclusions(self) -> None:
        section = self.text.split("## Notes", 1)[1]
        self.assertIn("`unresolved_calls`", section)
        self.assertIn("`Test*`, `Benchmark*`, `Fuzz*`, `Example*`", section)
        self.assertIn("`vendor/`, `testdata/`", section)

    def test_page_mapping_is_derived_when_not_given(self) -> None:
        self.assertEqual(render_project(self.result), self.text)


class PackageRenderTest(IndexTestCase):
    """``packages/<slug>.md``: one page per package."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()
        self.records = {record.file: record for record in self.result.files}
        self.pages = {
            package.key: render_package(package, self.records)
            for package in self.result.iter_packages()
        }

    def page(self, key: str) -> str:
        self.assertIn(key, self.pages, f"missing package {key}")
        return self.pages[key]

    def section(self, key: str, heading: str) -> str:
        """The slice of a page between ``heading`` and the next ``##`` heading."""
        text = self.page(key)
        self.assertIn(heading, text, f"missing {heading} in {key}")
        return text.split(heading, 1)[1].split("\n## ", 1)[0]

    def test_header_lists_path_directory_files_and_symbols(self) -> None:
        store = self.page("example.com/demo/internal/store|store")
        self.assertTrue(store.startswith("# Package `store`\n"))
        facts = store.splitlines()[2]
        self.assertIn("path `example.com/demo/internal/store`", facts)
        self.assertIn("dir `internal/store`", facts)
        self.assertIn("2 file(s)", facts)
        self.assertIn("14 symbol(s)", facts)

    def test_root_package_has_no_directory(self) -> None:
        self.assertNotIn("dir `", self.page("example.com/demo|main").splitlines()[2])

    def test_package_docs_are_quoted(self) -> None:
        self.assertIn(
            "> Package store keeps items in memory.",
            self.page("example.com/demo/internal/store|store"),
        )
        self.assertIn(
            "> Package api adapts the store for other layers.",
            self.page("example.com/demo/internal/api|api"),
        )

    def test_test_packages_are_marked(self) -> None:
        facts = self.page("example.com/demo/internal/api_test|api_test").splitlines()[2]
        self.assertIn("test package", facts)

    def test_files_table_counts_lines_and_symbols(self) -> None:
        files = self.section("example.com/demo/internal/store|store", "## Files")
        self.assertIn("| File | Lines | Symbols |", files)
        self.assertIn("| `internal/store/store.go` | 70 | 13 |", files)
        self.assertIn("| `internal/store/store_test.go` | 15 | 1 |", files)

    def test_files_table_gains_a_build_tag_column_when_needed(self) -> None:
        platform = self.section("example.com/demo/platform|platform", "## Files")
        self.assertIn("| File | Lines | Symbols | Build tags |", platform)
        self.assertIn("| `platform/mode_linux.go` | 7 | 1 | go:build linux |", platform)
        self.assertIn("| `platform/mode_windows.go` | 7 | 1 | go:build windows |", platform)
        # A package without tagged files keeps the narrow table.
        self.assertNotIn(
            "Build tags", self.section("example.com/demo/internal/store|store", "## Files")
        )

    def test_files_table_without_records_leaves_the_line_count_empty(self) -> None:
        package = next(
            item
            for item in self.result.iter_packages()
            if item.key == "example.com/demo/internal/store|store"
        )
        text = render_package(package)
        self.assertIn("| `internal/store/store.go` |  | 13 |", text)

    def test_build_tags_come_from_the_file_records(self) -> None:
        package = PackageInfo(
            path="example.com/demo/p", name="p", directory="p", files=["p/a.go"]
        )
        record = FileRecord(
            file="p/a.go",
            package="p",
            package_path="example.com/demo/p",
            lines=3,
            symbols=0,
            build_tags=["go:build linux", "go:build amd64"],
        )
        text = render_package(package, {"p/a.go": record})
        self.assertIn("| File | Lines | Symbols | Build tags |", text)
        self.assertIn("| `p/a.go` | 3 | 0 | go:build linux; go:build amd64 |", text)

    def test_build_tag_column_is_absent_without_file_records(self) -> None:
        package = PackageInfo(
            path="example.com/demo/p", name="p", directory="p", files=["p/a.go"]
        )
        text = render_package(package)
        self.assertNotIn("Build tags", text)

    def test_imports_are_sorted_and_deduplicated(self) -> None:
        imports = self.section("example.com/demo/internal/store|store", "## Imports")
        self.assertEqual(
            [line for line in imports.splitlines() if line.startswith("- ")],
            ["- `context`", "- `fmt`", "- `sync`", "- `testing`"],
        )

    def test_imports_section_is_omitted_for_packages_without_imports(self) -> None:
        self.assertNotIn("## Imports", self.page("example.com/demo/broken|broken"))
        self.assertNotIn("## Imports", self.page("example.com/demo/platform|platform"))


    def test_symbol_sections_follow_the_kind_order(self) -> None:
        store = self.page("example.com/demo/internal/store|store")
        headings = [
            line.split()[1] for line in store.splitlines() if line.startswith("## ")
        ]
        self.assertEqual(
            headings,
            [
                "Files",
                "Imports",
                "Structs",
                "Interfaces",
                "Types",
                "Functions",
                "Tests",
                "Methods",
                "Constants",
                "Variables",
            ],
        )

    def test_struct_lists_fields_methods_and_used_types(self) -> None:
        structs = self.section("example.com/demo/internal/store|store", "## Structs")
        self.assertIn("### `Item`", structs)
        self.assertIn("```go\ntype Item struct\n```", structs)
        self.assertIn("> Item is one stored value.", structs)
        self.assertIn(
            "*struct* · `internal/store/store.go` lines 11-14 · "
            "id `struct:example.com/demo/internal/store:Item`",
            structs,
        )
        self.assertIn("- **fields**: `ID string`; `Kind Kind`", structs)
        self.assertIn(
            "- **methods**: `memory.Get`, `memory.Put`, `memory.lookup`, `memory.save`",
            structs,
        )

    def test_unexported_symbols_are_flagged(self) -> None:
        structs = self.section("example.com/demo/internal/store|store", "## Structs")
        self.assertIn(
            "*struct* · `internal/store/store.go` lines 38-41 · "
            "id `struct:example.com/demo/internal/store:memory` · unexported",
            structs,
        )

    def test_interface_lists_method_signatures(self) -> None:
        interfaces = self.section("example.com/demo/internal/store|store", "## Interfaces")
        self.assertIn("### `Store`", interfaces)
        self.assertIn(
            "- **method signatures**: `Get(ctx context.Context, id string) (Item, error)`",
            interfaces,
        )
        self.assertIn("- **uses types**: `Item`", interfaces)

    def test_same_package_references_are_shortened(self) -> None:
        methods = self.section("example.com/demo/internal/store|store", "## Methods")
        self.assertIn("### `memory.Get`", methods)
        self.assertIn("- **calls**: `memory.lookup`", methods)
        self.assertIn("- **uses types**: `Item`, `memory`", methods)

    def test_cross_package_references_keep_full_ids(self) -> None:
        structs = self.section("example.com/demo/internal/api|api", "## Structs")
        self.assertIn(
            "- **uses types**: `interface:example.com/demo/internal/store:Store`", structs
        )
        functions = self.section("example.com/demo/internal/api|api", "## Functions")
        self.assertIn(
            "- **called by**: `test:example.com/demo/internal/api_test:TestHandlerGet`",
            functions,
        )

    def test_caller_in_the_same_package_stays_bare(self) -> None:
        functions = self.section("example.com/demo|main", "## Functions")
        self.assertIn("- **called by**: `main`", functions)

    def test_unresolved_calls_are_marked_with_a_question_mark(self) -> None:
        functions = self.section("example.com/demo|main", "## Functions")
        self.assertIn(
            "- **unresolved calls**: `?context.Background`, `?fmt.Println`", functions
        )

    def test_duplicate_names_get_file_suffixed_ids(self) -> None:
        constants = self.section("example.com/demo/platform|platform", "## Constants")
        self.assertEqual(constants.count("### `Mode`"), 2)
        self.assertIn("const:example.com/demo/platform:Mode#platform/mode_linux.go", constants)
        self.assertIn("const:example.com/demo/platform:Mode#platform/mode_windows.go", constants)

    def test_signatures_and_docs_are_verbatim(self) -> None:
        text = self.page("example.com/demo/broken|broken")
        self.assertIn('```go\nvar BrokenValue = "unterminated\n```', text)
        self.assertIn("> StillParsed is recovered after the broken literal.", text)

    def test_footer_explains_bare_names(self) -> None:
        for text in self.pages.values():
            self.assertIn("kind:package-path:Name", text)

    def test_page_ends_with_a_single_newline(self) -> None:
        for text in self.pages.values():
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))


class WriteMarkdownTest(IndexTestCase):
    """:func:`write_markdown` writes PROJECT.md plus one page per package."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()
        self.records = {record.file: record for record in self.result.files}
        self.pages = package_pages(self.result.packages.values())

    def test_writes_the_project_page_and_every_package_page(self) -> None:
        written = write_markdown(self.index_dir, self.result, self.pages)
        self.assertEqual(len(written), len(self.result.packages) + 1)
        self.assertEqual(written[0], os.path.join(self.index_dir, "PROJECT.md"))
        self.assertEqual(len(set(written)), len(written))
        for path in written:
            self.assertTrue(os.path.isfile(path), f"missing {path}")

    def test_project_page_matches_render_project(self) -> None:
        write_markdown(self.index_dir, self.result, self.pages)
        self.assertEqual(
            read_text(os.path.join(self.index_dir, "PROJECT.md")),
            render_project(self.result, self.pages),
        )

    def test_package_page_matches_render_package(self) -> None:
        package = next(
            item
            for item in self.result.iter_packages()
            if item.key == "example.com/demo/internal/store|store"
        )
        write_markdown(self.index_dir, self.result, self.pages)
        path = os.path.join(self.index_dir, self.pages[package.key])
        self.assertEqual(read_text(path), render_package(package, self.records))

    def test_nested_page_directories_are_created(self) -> None:
        paths = write_markdown(self.index_dir, self.result, self.pages)
        nested = [path for path in paths if os.path.dirname(path) != self.index_dir]
        self.assertTrue(nested)
        self.assertTrue(all(os.path.isdir(os.path.dirname(path)) for path in nested))

    def test_pages_mapping_is_derived_when_not_given(self) -> None:
        written = write_markdown(self.index_dir, self.result)
        self.assertIn(os.path.join(self.index_dir, "packages", "internal_store.md"), written)

    def test_explicit_mapping_is_respected(self) -> None:
        mapping = {key: f"pages/{key.split('|')[1]}.md" for key in self.pages}
        write_markdown(self.index_dir, self.result, mapping)
        for relative in mapping.values():
            self.assertTrue(
                os.path.isfile(os.path.join(self.index_dir, relative)), relative
            )

    def test_rewriting_is_idempotent(self) -> None:
        first = write_markdown(self.index_dir, self.result, self.pages)
        before = {path: read_text(path) for path in first}
        write_markdown(self.index_dir, self.result, self.pages)
        self.assertEqual(before, {path: read_text(path) for path in first})

    def test_pages_end_with_a_newline_and_use_lf(self) -> None:
        for path in write_markdown(self.index_dir, self.result, self.pages):
            body = read_text(path)
            self.assertTrue(body.endswith("\n"))
            self.assertNotIn("\r", body)


if __name__ == "__main__":
    unittest.main()
