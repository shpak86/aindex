"""The ``aindex-query`` command line: exit codes, reports and the JSON contract.

Everything here runs the CLI in-process against a real index of the demo
project (:mod:`tests.query_support`), so the numbers and ids the assertions use
are the ones :mod:`aindex` actually wrote.  The only exceptions are the few
tests that exercise the installed entry point through ``subprocess``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from typing import Any

from aindex_query import __version__
from aindex_query.errors import (
    EXIT_INDEX_NOT_FOUND,
    EXIT_INVALID_INDEX,
    EXIT_OK,
    EXIT_SECURITY,
    EXIT_SOURCE_READ_ERROR,
    EXIT_SYMBOL_NOT_FOUND,
    EXIT_USAGE,
)
from tests.query_support import QueryTestCase, run_query

#: The repository root: ``subprocess`` runs of ``aindex-query`` need it.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The entry point script, run as an agent would run it.
LAUNCHER = os.path.join(REPO_ROOT, "aindex-query")


class UsageTest(unittest.TestCase):
    """The command line itself: ``--help``, ``--version`` and argparse errors."""

    def test_no_command_prints_help_and_succeeds(self) -> None:
        status, out, err = run_query([])
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("COMMAND", out)
        self.assertIn("exit codes: 0 ok", out)

    def test_help_lists_every_command(self) -> None:
        status, out, err = run_query(["--help"])
        self.assertEqual((status, err), (EXIT_OK, ""))
        for command in ("overview", "packages", "package", "search", "symbol",
                        "callers", "callees", "read", "doctor"):
            self.assertIn(command, out)

    def test_version(self) -> None:
        status, out, err = run_query(["--version"])
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(out.strip(), f"aindex-query {__version__}")
        self.assertEqual(err, "")

    def test_unknown_command_is_a_usage_error(self) -> None:
        status, out, err = run_query(["bogus"])
        self.assertEqual(status, EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("invalid choice: 'bogus'", err)

    def test_missing_argument_is_a_usage_error(self) -> None:
        for command in ("search", "symbol", "package", "callers", "callees", "read"):
            with self.subTest(command=command):
                status, out, err = run_query([command])
                self.assertEqual(status, EXIT_USAGE, err)
                self.assertIn("usage: aindex-query", err)

    def test_unknown_kind_is_a_usage_error(self) -> None:
        status, out, err = run_query(["search", "New", "--kind", "banana"])
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("invalid choice: 'banana'", err)


class LimitUsageTest(QueryTestCase):
    """Limits are validated once an index exists, before any work happens."""

    def test_negative_limits_are_usage_errors(self) -> None:
        cases = [
            ["overview", "--limit", "-1"],
            ["overview", "--packages", "-1"],
            ["search", "New", "--limit", "-1"],
            ["package", "store", "--limit", "-1"],
            ["callers", "New", "--limit", "-1"],
            ["callees", "New", "--limit", "-1"],
            ["read", "New", "--limit", "-1"],
            ["read", "New", "--context", "-1"],
            ["doctor", "--limit", "-1"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                status, out, err = self.query(*argv)
                self.assertEqual(status, EXIT_USAGE, err)
                self.assertIn("cannot be negative", err)


class IndexLocationTest(QueryTestCase):
    """Where the index is looked for, and what happens when it is not there."""

    def test_missing_index_exits_two(self) -> None:
        empty = os.path.join(self._tmp.name, "no-index-here")
        os.makedirs(empty, exist_ok=True)
        status, out, err = run_query(["--root", empty, "overview"])
        self.assertEqual((status, out), (EXIT_INDEX_NOT_FOUND, ""))
        self.assertIn("index directory not found", err)

    def test_directory_without_symbols_exits_two(self) -> None:
        os.makedirs(os.path.join(self.root, "half"), exist_ok=True)
        status, _, err = run_query(["--root", self.root, "--index", "half", "overview"])
        self.assertEqual(status, EXIT_INDEX_NOT_FOUND)
        self.assertIn("missing symbols.jsonl", err)

    def test_explicit_index_flag_wins(self) -> None:
        moved = os.path.join(self._tmp.name, "elsewhere")
        os.rename(self.index_dir, moved)
        status, out, err = run_query(["--root", self.root, "--index", moved, "packages"])
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("example.com/demo/internal/store", out)
        # ... and the default location is now genuinely missing.
        status, _, err = run_query(["--root", self.root, "packages"])
        self.assertEqual(status, EXIT_INDEX_NOT_FOUND)
        self.assertIn("index directory not found", err)

    def test_environment_variable_is_honoured(self) -> None:
        moved = os.path.join(self._tmp.name, "from-env")
        os.rename(self.index_dir, moved)
        saved = os.environ.get("AINDEX_INDEX_DIR")
        os.environ["AINDEX_INDEX_DIR"] = moved
        try:
            status, out, err = run_query(["--root", self.root, "overview"])
        finally:
            if saved is None:
                os.environ.pop("AINDEX_INDEX_DIR", None)
            else:
                os.environ["AINDEX_INDEX_DIR"] = saved
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn(moved, out)

    def test_missing_stats_reports_derived_totals(self) -> None:
        os.remove(self.index_file("stats.json"))
        status, out, err = run_query(["--root", self.root, "overview"])
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("stats.json MISSING (values derived)", out)
        self.assertIn("totals:", out)


class OverviewTest(QueryTestCase):
    """``overview``: the orientation page an agent reads first."""

    def test_orientation_lines(self) -> None:
        out = self.ok("overview")
        self.assertIn(f"project: {self.root}", out)
        self.assertIn(f"index:   {self.index_dir}", out)
        self.assertIn("source: default (.ai-index), generated:", out)
        self.assertIn("module:  example.com/demo (go 1.21.5)", out)
        self.assertIn("totals:  6 packages, 10 files, 28 symbols", out)
        self.assertIn("calls:   10 resolved to a symbol id, 11 left as raw names", out)
        # The demo project has two files the lexer/parser complains about, but a
        # clean index: no malformed JSONL lines.
        self.assertIn("health:  stats.json; 2 go files with parse errors; index clean", out)

    def test_package_table_mentions_the_orphan_group(self) -> None:
        out = self.ok("overview")
        self.assertIn("packages (7, 1 without a package clause):", out)
        for import_path in (
            "example.com/demo",
            "example.com/demo/broken",
            "example.com/demo/internal/api",
            "example.com/demo/internal/api_test",
            "example.com/demo/internal/store",
            "example.com/demo/orphan",
            "example.com/demo/platform",
        ):
            with self.subTest(import_path=import_path):
                self.assertIn(import_path, out)
        self.assertIn("(root)", out)
        self.assertIn("[has tests]", out)
        self.assertIn("[no package clause]", out)

    def test_entry_points_are_exported_declarations(self) -> None:
        out = self.ok("overview")
        self.assertIn("entry points (9 of 9 exported declarations):", out)
        self.assertIn("struct Item  internal/store/store.go:11-14", out)
        self.assertIn("func NewHandler  internal/api/api.go:26-28", out)

    def test_flags_cap_packages_and_entry_points(self) -> None:
        out = self.ok("overview", "--packages", "2", "--limit", "3")
        self.assertIn("showing 2 of 7 (use --limit 0 for all)", out)
        self.assertIn("entry points (3 of 9 exported declarations):", out)
        self.assertIn("showing 3 of 9 (use --limit 0 for all)", out)

    def test_zero_means_no_limit(self) -> None:
        out = self.ok("overview", "--packages", "0", "--limit", "0")
        self.assertNotIn("showing", out)
        self.assertIn("entry points (9 of 9 exported declarations):", out)

    def test_json_document(self) -> None:
        status, payload = self.query_json("overview", "--packages", "0", "--limit", "0")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["project_root"], self.root)
        self.assertEqual(payload["index_dir"], self.index_dir)
        self.assertFalse(payload["explicit_index"])
        self.assertFalse(payload["stats_missing"])
        self.assertEqual(payload["problems"], [])
        self.assertEqual(payload["stats"]["packages"], 6)
        self.assertEqual(len(payload["packages"]), 7)
        self.assertEqual(len(payload["entry_points"]), 9)
        paths = [group["package_path"] for group in payload["packages"]]
        self.assertEqual(paths, sorted(paths))

    def test_stats_missing_derives_totals(self) -> None:
        os.remove(self.index_file("stats.json"))
        status, out, err = self.query("overview")
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("stats.json MISSING (values derived)", out)
        self.assertIn("totals:  6 packages, 10 files, 28 symbols", out)


class PackagesTest(QueryTestCase):
    """``packages``: every package at a glance."""

    def test_table_and_pages(self) -> None:
        out = self.ok("packages")
        self.assertIn("packages (7, 1 without a package clause):", out)
        rows = {line.split()[0] for line in out.splitlines() if line.startswith("  ") and "files" in line}
        self.assertEqual(rows, {
            "(root)", "broken", "internal/api", "internal/store", "orphan", "platform"
        })
        self.assertIn("api_test    1 files     1 symbols  example.com/demo/internal/api_test  [tests]", out)
        self.assertIn(
            "page per package: packages/root.md, packages/broken.md, "
            "packages/internal_api.md, ...",
            out,
        )
        self.assertNotIn("/tmp", out)  # paths stay relative to the index

    def test_json_document(self) -> None:
        status, payload = self.query_json("packages")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["count"], 7)
        self.assertEqual(len(payload["packages"]), 7)
        self.assertEqual(len(payload["pages"]), 7)
        by_path = {group["package_path"]: group for group in payload["packages"]}
        api_test = by_path["example.com/demo/internal/api_test"]
        self.assertEqual(api_test["name"], "api_test")
        self.assertTrue(api_test["test_package"])
        self.assertEqual(api_test["directory"], "internal/api")
        self.assertEqual(api_test["symbols"], 1)
        # The root package has no directory of its own.
        self.assertEqual(by_path["example.com/demo"]["directory"], "")
        self.assertEqual(
            payload["pages"]["example.com/demo/internal/api_test|api_test"],
            "internal_api-test",
        )


class PackageTest(QueryTestCase):
    """``package``: one package in detail."""

    def test_by_name(self) -> None:
        out = self.ok("package", "store")
        self.assertIn("package store  (example.com/demo/internal/store)", out)
        self.assertIn("directory:  internal/store", out)
        self.assertIn("symbols:    14", out)
        self.assertIn("index page: .ai-index/packages/internal_store.md", out)
        self.assertIn("Package store keeps items in memory.", out)
        self.assertIn("files (2):", out)
        self.assertIn("internal/store/store_test.go", out)

    def test_by_import_path_matches_the_name(self) -> None:
        self.assertEqual(
            self.ok("package", "example.com/demo/internal/store"),
            self.ok("package", "store"),
        )

    def test_external_test_package_is_its_own_group(self) -> None:
        out = self.ok("package", "api_test")
        self.assertIn("package api_test  (example.com/demo/internal/api_test)", out)
        self.assertIn("symbols:    1 (including tests)", out)
        self.assertIn("tests (1):", out)
        self.assertIn("test TestHandlerGet  internal/api/api_test.go:10-15", out)
        self.assertIn("internal/api/api_test.go     1 symbol    16 lines  [test]", out)

    def test_unknown_package_exits_three(self) -> None:
        status, out, err = self.fails("package", "nope")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("error: no package matches 'nope'", err)
        self.assertIn("lists all 7 packages", err)

    def test_suffix_match_accepts_a_directory(self) -> None:
        out = self.ok("package", "internal/store")
        self.assertIn("package store  (example.com/demo/internal/store)", out)

    def test_ambiguous_name_lists_candidates(self) -> None:
        status, out, err = self.fails("package", "internal")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("ambiguous package: internal", err)
        for name in ("api", "api_test", "store"):
            with self.subTest(name=name):
                self.assertIn(name, err)

    def test_ambiguous_name_json(self) -> None:
        status, payload = self.query_json("package", "internal")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertFalse(payload["found"])
        self.assertTrue(payload["ambiguous"])
        self.assertEqual(payload["query"], "internal")
        self.assertEqual(len(payload["candidates"]), 3)

    def test_limit_caps_the_symbols_shown_per_section(self) -> None:
        out = self.ok("package", "internal/api", "--limit", "1")
        self.assertIn("structs (2):", out)
        self.assertIn("... and 1 more", out)
        self.assertIn("funcs (2):", out)
        self.assertIn("... and 2 more", out)
        self.assertIn("type Handler struct", out)
        # With no limit every symbol of the section is printed.
        wide = self.ok("package", "internal/api")
        self.assertNotIn("more", wide)

    def test_json_document(self) -> None:
        status, payload = self.query_json("package", "api_test")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            payload["package"]["package_path"], "example.com/demo/internal/api_test"
        )
        self.assertTrue(payload["package"]["test_package"])
        self.assertEqual(len(payload["files"]), 1)
        self.assertEqual(payload["files"][0]["file"], "internal/api/api_test.go")
        self.assertEqual(len(payload["symbols"]), 1)
        self.assertEqual(payload["package_doc"], "")
        self.assertEqual(
            payload["package_page"], self.index_file("packages", "internal_api-test.md")
        )


class SearchTest(QueryTestCase):
    """``search``: ranked matching plus the filter flags."""

    def test_ranks_exact_name_matches_first(self) -> None:
        out = self.ok("search", "store")
        self.assertIn("query: store", out)
        self.assertIn("21 matches, showing 10; raise --limit for more", out)
        store_interface = out.index("interface:example.com/demo/internal/store:Store")
        store_package = out.index("package:example.com/demo/internal/store:store")
        new_handler = out.index("func:example.com/demo/internal/api:NewHandler")
        self.assertLess(store_interface, store_package)
        self.assertLess(store_package, new_handler)
        self.assertIn("0.9800  package:example.com/demo/internal/store:store", out)

    def test_limit_zero_shows_everything(self) -> None:
        out = self.ok("search", "New", "--limit", "0")
        self.assertIn("5 matches, showing 5", out)
        self.assertNotIn("raise --limit for more", out)

    def test_limit_truncates(self) -> None:
        out = self.ok("search", "New", "--limit", "2")
        self.assertIn("5 matches, showing 2; raise --limit for more", out)
        self.assertNotIn("TestHandlerGet", out)

    def test_kind_filter(self) -> None:
        out = self.ok("search", "New", "--kind", "func")
        self.assertIn("3 matches, showing 3", out)
        self.assertIn("func:example.com/demo/internal/store:New", out)
        self.assertIn("func:example.com/demo:run", out)
        self.assertNotIn("test:", out)

    def test_exported_and_no_tests_filters(self) -> None:
        out = self.ok("search", "New", "--exported", "--no-tests")
        self.assertIn("2 matches, showing 2", out)
        self.assertNotIn("test:", out)

    def test_no_tests_filter_drops_test_symbols_only(self) -> None:
        with_tests = self.ok("search", "TestMemoryGet", "--limit", "0")
        without = self.ok("search", "TestMemoryGet", "--no-tests", "--limit", "0")
        self.assertIn("test:example.com/demo/internal/store:TestMemoryGet", with_tests)
        self.assertNotIn("test:example.com/demo/internal/store:TestMemoryGet", without)

    def test_package_filter_narrows_to_an_import_path(self) -> None:
        out = self.ok("search", "Handler", "--package", "internal/api")
        self.assertIn("3 matches, showing 3", out)
        self.assertNotIn("example.com/demo/internal/store:", out)

    def test_package_filter_accepts_a_short_name(self) -> None:
        out = self.ok("search", "New", "--package", "store")
        self.assertIn("2 matches, showing 2", out)
        self.assertIn("test:example.com/demo/internal/store:TestMemoryGet", out)

    def test_package_filter_must_exist(self) -> None:
        status, out, err = self.fails("search", "New", "--package", "nope")
        self.assertEqual((status, out), (EXIT_USAGE, ""))
        self.assertIn("error: --package 'nope' matches no indexed package", err)
        self.assertIn("`aindex-query packages`", err)

    def test_file_filter(self) -> None:
        out = self.ok("search", "Handler", "--file", "api.go")
        self.assertIn("3 matches, showing 3", out)
        self.assertIn("internal/api/api.go:21-23", out)

    def test_file_filter_must_be_indexed(self) -> None:
        status, out, err = self.fails("search", "Handler", "--file", "nope.go")
        self.assertEqual((status, out), (EXIT_USAGE, ""))
        self.assertIn("error: --file 'nope.go' is not in the index", err)

    def test_no_match_exits_three(self) -> None:
        status, out, err = self.query("search", "zzz")
        self.assertEqual((status, err), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("query: zzz", out)
        self.assertIn("no symbol matched", out)
        self.assertIn("hint: try a shorter query", out)

    def test_no_match_with_filters_hints_at_the_filters(self) -> None:
        status, out, err = self.fails("search", "zzz", "--kind", "func")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertIn("no symbol matched", out)
        self.assertIn("hint: filters were active", err)
        self.assertIn("--kind/--package/--file/--exported/--no-tests", err)

    def test_filters_can_hide_every_match(self) -> None:
        # ``Mode`` is a const, so asking for vars finds nothing ...
        status, out, err = self.fails("search", "Mode", "--kind", "var")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertIn("no symbol matched", out)
        self.assertIn("hint: filters were active", err)

    def test_json_document_echoes_the_filters(self) -> None:
        status, payload = self.query_json("search", "Handler", "--package", "internal/api")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["query"], "Handler")
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["filters"]["package_path"], "example.com/demo/internal/api")
        self.assertEqual(payload["filters"]["include_tests"], True)
        self.assertEqual(payload["filters"]["kinds"], [])
        ids = [result["id"] for result in payload["results"]]
        self.assertEqual(
            ids,
            [
                "struct:example.com/demo/internal/api:Handler",
                "method:example.com/demo/internal/api:Handler.Get",
                "func:example.com/demo/internal/api:NewHandler",
            ],
        )
        self.assertEqual(payload["results"][0]["score"], 0.98)



class SymbolTest(QueryTestCase):
    """``symbol``: one record in full."""

    def test_every_field_of_a_method_is_printed(self) -> None:
        symbol_id = "method:example.com/demo/internal/store:memory.Get"
        out = self.ok("symbol", symbol_id)
        self.assertIn(f"symbol: {symbol_id}", out)
        self.assertIn("kind:       method", out)
        self.assertIn("package:    store  (example.com/demo/internal/store)", out)
        self.assertIn("file:       internal/store/store.go:49-55", out)
        self.assertIn("exported:   yes", out)
        self.assertIn("receiver:   *memory  (memory)", out)
        self.assertIn("func (m *memory) Get(ctx context.Context, id string) (Item, error)", out)
        self.assertIn("Get implements Store.", out)
        self.assertIn("types (2):", out)
        self.assertIn("struct:example.com/demo/internal/store:Item", out)
        self.assertIn("imports (3):", out)
        self.assertIn("calls (resolved to ids) (1):", out)
        self.assertIn("method:example.com/demo/internal/store:memory.lookup", out)
        # ``memory.Get`` is called by ``run`` and by the store's own test.
        self.assertIn("callers (from the whole index) (2):", out)
        self.assertIn("func:example.com/demo:run", out)
        self.assertIn("test:example.com/demo/internal/store:TestMemoryGet", out)

    def test_short_name_resolves_when_unambiguous(self) -> None:
        self.assertEqual(
            self.ok("symbol", "memory.Get"),
            self.ok("symbol", "method:example.com/demo/internal/store:memory.Get"),
        )

    def test_test_symbol_is_flagged(self) -> None:
        out = self.ok("symbol", "TestMemoryGet")
        self.assertIn("symbol: test:example.com/demo/internal/store:TestMemoryGet", out)
        self.assertIn("kind:       test", out)
        self.assertIn("exported:   yes   test_related: yes", out)
        self.assertIn("func TestMemoryGet(t *testing.T)", out)

    def test_unresolved_calls_are_listed_with_a_reason(self) -> None:
        out = self.ok("symbol", "func:example.com/demo:main")
        self.assertIn("  unresolved calls (2):", out)
        self.assertIn(
            "    context.Background  [raw name: stdlib, another package or a field]", out
        )
        self.assertIn("    fmt.Println  [raw name: stdlib, another package or a field]", out)

    def test_symbol_without_raw_names_omits_the_section(self) -> None:
        out = self.ok("symbol", "method:example.com/demo/internal/store:memory.Get")
        self.assertNotIn("unresolved calls", out)

    def test_ambiguous_name_exits_three(self) -> None:
        status, out, err = self.fails("symbol", "Get")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("ambiguous: Get", err)
        self.assertIn("2 symbols match; pass one of these ids exactly:", err)
        self.assertIn("method:example.com/demo/internal/api:Handler.Get", err)
        self.assertIn("method:example.com/demo/internal/store:memory.Get", err)

    def test_ambiguous_name_json_lists_candidates(self) -> None:
        status, payload = self.query_json("symbol", "Get")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertFalse(payload["found"])
        self.assertTrue(payload["ambiguous"])
        self.assertEqual(payload["query"], "Get")
        self.assertEqual(
            [candidate["id"] for candidate in payload["candidates"]],
            [
                "method:example.com/demo/internal/api:Handler.Get",
                "method:example.com/demo/internal/store:memory.Get",
            ],
        )

    def test_limit_caps_the_candidate_list(self) -> None:
        status, payload = self.query_json("symbol", "Get", "--limit", "1")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertEqual(len(payload["candidates"]), 1)

    def test_unknown_reference_exits_three_with_an_id_example(self) -> None:
        status, out, err = self.fails("symbol", "nope.Nope")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("not found: nope.Nope", err)
        self.assertIn("no symbol matches this reference; ids look like", err)
        self.assertIn("func:example.com/demo/internal/store:New", err)
        self.assertIn("try `aindex-query search nope.Nope` to rank near-misses", err)

    def test_json_document_matches_the_text(self) -> None:
        status, payload = self.query_json(
            "symbol", "method:example.com/demo/internal/store:memory.Get"
        )
        self.assertEqual(status, EXIT_OK)
        record = payload["symbol"]
        self.assertEqual(record["id"], "method:example.com/demo/internal/store:memory.Get")
        self.assertEqual(record["kind"], "method")
        self.assertEqual(record["receiver"], "*memory")
        self.assertEqual(record["receiver_type"], "memory")
        self.assertEqual(record["start_line"], 49)
        self.assertEqual(record["end_line"], 55)
        self.assertEqual(
            record["callers"],
            [
                "func:example.com/demo:run",
                "test:example.com/demo/internal/store:TestMemoryGet",
            ],
        )
        self.assertEqual(
            [target["id"] for target in payload["calls"]],
            ["method:example.com/demo/internal/store:memory.lookup"],
        )
        self.assertEqual(payload["dangling_calls"], [])
        self.assertEqual(
            [caller["id"] for caller in payload["callers"]],
            [
                "func:example.com/demo:run",
                "test:example.com/demo/internal/store:TestMemoryGet",
            ],
        )
        self.assertEqual(record["unresolved_calls"], [])

    def test_json_records_the_raw_call_names_of_the_symbol(self) -> None:
        status, payload = self.query_json("symbol", "func:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            payload["symbol"]["unresolved_calls"], ["context.Background", "fmt.Println"]
        )


#: A call target no record in the fixture index defines: an id that dangles.
GHOST_CALL = "func:example.com/demo/ghost:Vanished"


def _point_main_at_a_ghost(record: dict[str, Any]) -> dict[str, Any]:
    """Add :data:`GHOST_CALL` to ``main``'s call list (a stale index)."""
    if record.get("id") == "func:example.com/demo:main":
        record["calls"] = [*record.get("calls", []), GHOST_CALL]
    return record


class EdgesTest(QueryTestCase):
    """``callers`` / ``callees``: the two directions of the call graph."""

    def test_callees_of_a_method_are_resolved_ids(self) -> None:
        out = self.ok("callees", "memory.Get")
        self.assertIn("callees of: method:example.com/demo/internal/store:memory.Get", out)
        self.assertIn("  1 found", out)
        self.assertIn("  symbols (1):", out)
        self.assertIn("    method:example.com/demo/internal/store:memory.lookup", out)

    def test_callers_of_the_callee_point_back(self) -> None:
        out = self.ok("callers", "memory.lookup")
        self.assertIn("callers of: method:example.com/demo/internal/store:memory.lookup", out)
        self.assertIn("    method:example.com/demo/internal/store:memory.Get", out)

    def test_a_reference_may_be_spelled_any_way_symbol_accepts(self) -> None:
        self.assertEqual(
            self.ok("callers", "memory.lookup"),
            self.ok("callers", "method:example.com/demo/internal/store:memory.lookup"),
        )

    def test_callers_come_from_the_whole_index(self) -> None:
        out = self.ok("callers", "New")
        listed = [
            line.strip()
            for line in out.splitlines()
            if line.strip().startswith(("func:", "method:", "test:"))
        ]
        self.assertEqual(
            listed,
            [
                "func:example.com/demo:run",
                "test:example.com/demo/internal/store:TestMemoryGet",
            ],
        )

    def test_a_symbol_nothing_calls_explains_the_blind_spots(self) -> None:
        # ``Map`` is generic and exported; nothing inside the index calls it.
        out = self.ok("callers", "func:example.com/demo/internal/api:Map")
        self.assertIn("  0 found", out)
        self.assertIn("  (nothing in the index points this way)", out)
        self.assertIn(
            "the index resolves calls inside the indexed packages; "
            "unexported entry points and reflection stay invisible",
            out,
        )

    def test_raw_call_names_are_listed_next_to_resolved_ids(self) -> None:
        out = self.ok("callees", "TestMemoryGet")
        self.assertIn("    func:example.com/demo/internal/store:New", out)
        self.assertIn("  unresolved calls (2):", out)
        for name in ("context.Background", "t.Fatalf"):
            self.assertIn(
                f"    {name}  [raw name: stdlib, another package or a field]", out
            )

    def test_callees_without_raw_names_omit_the_section(self) -> None:
        self.assertNotIn("unresolved calls", self.ok("callees", "memory.Get"))

    def test_raw_names_are_shown_even_when_no_id_resolves(self) -> None:
        out = self.ok("callees", "memory.lookup")
        self.assertIn("  0 found", out)
        self.assertIn("unresolved calls (2):", out)
        self.assertIn("    m.mu.Lock  [raw name: stdlib, another package or a field]", out)
        self.assertIn("m.mu.Unlock", out)

    def test_limit_caps_the_resolved_ids_only(self) -> None:
        out = self.ok("callers", "New", "--limit", "1")
        self.assertIn("  2 found", out)
        self.assertIn("  symbols (2):", out)
        self.assertIn("    ... and 1 more", out)
        raw = self.ok("callees", "TestMemoryGet", "--limit", "1")
        self.assertIn("    func:example.com/demo/internal/store:New", raw)
        self.assertIn("    ... and 2 more", raw)
        # Raw names are not ids, so ``--limit`` never hides them.
        self.assertIn("  unresolved calls (2):", raw)
        self.assertIn("    context.Background", raw)
        self.assertIn("    t.Fatalf", raw)

    def test_unknown_reference_exits_three(self) -> None:
        status, out, err = self.fails("callers", "nope.Nope")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("not found: nope.Nope", err)
        self.assertIn("try `aindex-query search nope.Nope` to rank near-misses", err)

    def test_ambiguous_reference_exits_three_with_candidates(self) -> None:
        status, out, err = self.fails("callees", "Get")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("ambiguous: Get", err)
        self.assertIn("2 symbols match; pass one of these ids exactly:", err)
        self.assertIn("method:example.com/demo/internal/api:Handler.Get", err)
        self.assertIn("method:example.com/demo/internal/store:memory.Get", err)

    def test_ambiguous_reference_json_lists_candidates(self) -> None:
        status, payload = self.query_json("callers", "Get")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertFalse(payload["found"])
        self.assertTrue(payload["ambiguous"])
        self.assertEqual(len(payload["candidates"]), 2)


    def test_json_callees_document(self) -> None:
        status, payload = self.query_json("callees", "func:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["query"], "func:example.com/demo:main")
        self.assertEqual(payload["symbol"], "func:example.com/demo:main")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["dangling_count"], 0)
        self.assertEqual(payload["unresolved_count"], 2)
        self.assertEqual(
            [target["id"] for target in payload["callees"]],
            ["func:example.com/demo:run"],
        )
        self.assertEqual(payload["callees"][0]["file"], "main.go")
        self.assertEqual(payload["dangling_calls"], [])
        self.assertEqual(payload["unresolved_calls"], ["context.Background", "fmt.Println"])

    def test_json_callers_document(self) -> None:
        status, payload = self.query_json("callers", "memory.Get")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            sorted(payload),
            ["callers", "count", "query", "symbol"],
        )
        self.assertEqual(payload["query"], "memory.Get")
        self.assertEqual(
            payload["symbol"], "method:example.com/demo/internal/store:memory.Get"
        )
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            [caller["id"] for caller in payload["callers"]],
            [
                "func:example.com/demo:run",
                "test:example.com/demo/internal/store:TestMemoryGet",
            ],
        )

    def test_json_callers_document_is_empty_for_an_uncalled_symbol(self) -> None:
        status, payload = self.query_json("callers", "func:example.com/demo/internal/api:Map")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["callers"], [])

    def test_a_call_id_missing_from_the_index_is_marked_dangling(self) -> None:
        self.edit_index_jsonl("symbols.jsonl", _point_main_at_a_ghost)
        out = self.ok("callees", "func:example.com/demo:main")
        self.assertIn("  2 found", out)
        self.assertIn("    func:example.com/demo:run", out)
        self.assertIn(f"    {GHOST_CALL}  [unresolved: not a known id]", out)
        # The record's own raw names stay a separate, differently worded list.
        self.assertIn("unresolved calls (2):", out)

    def test_dangling_ids_are_counted_but_not_resolved_in_json(self) -> None:
        self.edit_index_jsonl("symbols.jsonl", _point_main_at_a_ghost)
        status, payload = self.query_json("callees", "func:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["dangling_count"], 1)
        self.assertEqual(payload["dangling_calls"], [GHOST_CALL])
        self.assertEqual(payload["unresolved_count"], 2)
        self.assertEqual(
            [target["id"] for target in payload["callees"]],
            ["func:example.com/demo:run"],
        )

    def test_symbol_detail_reports_dangling_calls_too(self) -> None:
        self.edit_index_jsonl("symbols.jsonl", _point_main_at_a_ghost)
        out = self.ok("symbol", "func:example.com/demo:main")
        self.assertIn("calls (resolved to ids) (2):", out)
        self.assertIn(f"    {GHOST_CALL}  [unresolved: not a known id]", out)

    def test_json_symbol_document_exposes_dangling_calls(self) -> None:
        self.edit_index_jsonl("symbols.jsonl", _point_main_at_a_ghost)
        status, payload = self.query_json("symbol", "func:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["dangling_calls"], [GHOST_CALL])
        self.assertEqual(
            [target["id"] for target in payload["calls"]],
            ["func:example.com/demo:run"],
        )


class ReadTest(QueryTestCase):
    """``read``: source ranges from a symbol id or from a path."""

    def test_a_symbol_prints_its_own_range(self) -> None:
        out = self.ok("read", "memory.Get")
        self.assertIn("file: internal/store/store.go  (lines 49-55 of 69)", out)
        self.assertIn("49\tfunc (m *memory) Get(ctx context.Context, id string) (Item, error) {", out)
        self.assertIn("55\t}", out)

    def test_context_widens_the_symbol_range(self) -> None:
        out = self.ok("read", "memory.Get", "--context", "1")
        self.assertIn("file: internal/store/store.go  (lines 48-56 of 69)", out)
        self.assertIn("48\t// Get implements Store.", out)

    def test_a_whole_file_is_numbered_from_one(self) -> None:
        out = self.ok("read", "internal/store/store.go")
        self.assertIn("file: internal/store/store.go  (lines 1-69 of 69)", out)
        self.assertIn(" 1\t// Package store keeps items in memory.", out)
        self.assertIn("69\tvar _ Store = (*memory)(nil)", out)

    def test_a_path_suffix_selects_one_line(self) -> None:
        out = self.ok("read", "internal/store/store.go:12")
        self.assertIn("file: internal/store/store.go  (lines 12-69 of 69)", out)
        self.assertIn("12\t\tID   string", out)

    def test_a_path_suffix_selects_a_range(self) -> None:
        out = self.ok("read", "internal/store/store.go:12-20")
        self.assertIn("(lines 12-20 of 69)", out)
        numbered = [
            line for line in out.splitlines() if line.split("\t")[0].strip().isdigit()
        ]
        self.assertEqual(len(numbered), 9)
        self.assertIn("12\t\tID   string", out)
        self.assertIn("20\tconst (", out)
        self.assertNotIn("21\t", out)

    def test_start_and_end_flags_select_a_range(self) -> None:
        out = self.ok("read", "internal/store/store.go", "--start", "5", "--end", "8")
        self.assertIn("(lines 5-8 of 69)", out)
        self.assertIn("5\t\t\"context\"", out)
        self.assertIn("8\t)", out)

    def test_start_alone_runs_to_the_end_of_the_file(self) -> None:
        self.assertIn(
            "file: internal/store/store.go  (lines 5-69 of 69)",
            self.ok("read", "internal/store/store.go", "--start", "5"),
        )

    def test_context_is_clipped_at_the_file_edges(self) -> None:
        out = self.ok("read", "main.go:1-2", "--context", "5")
        self.assertIn("file: main.go  (lines 1-7 of 26)", out)

    def test_limit_truncates_and_says_how_to_see_more(self) -> None:
        out = self.ok("read", "internal/store/store.go", "--limit", "3")
        self.assertIn("(lines 1-3 of 69)", out)
        self.assertIn("  ... truncated after 3 lines (narrow the range or raise --limit)", out)

    def test_a_range_past_the_end_of_the_file_is_not_an_error(self) -> None:
        out = self.ok("read", "internal/store/store.go:999")
        self.assertIn("file: internal/store/store.go  (69 lines)", out)
        self.assertIn("the file has 69 lines; the range starts past its end", out)

    def test_an_empty_file_is_reported_as_empty(self) -> None:
        self.write_project_file("empty.go", "")
        out = self.ok("read", "empty.go")
        self.assertIn("file: empty.go  (0 lines)", out)
        self.assertIn("the file is empty", out)

    def test_a_file_the_indexer_only_parsed_in_part_warns_about_it(self) -> None:
        out = self.ok("read", "broken/broken.go")
        self.assertIn(
            "note: the indexer recorded a parse error in this file; "
            "the lines below come from the file itself",
            out,
        )
        self.assertIn("file: broken/broken.go  (lines 1-7 of 7)", out)
        self.assertIn("4\tvar BrokenValue = \"unterminated", out)

    def test_a_package_record_has_no_range_to_read(self) -> None:
        status, out, err = self.fails("read", "package:example.com/demo:main")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("'package:example.com/demo:main' has no source file in the index", err)
        self.assertIn("package records carry no source range", err)

    def test_something_that_is_neither_a_path_nor_a_symbol_exits_three(self) -> None:
        status, out, err = self.fails("read", "go.mod")
        self.assertEqual((status, out), (EXIT_SYMBOL_NOT_FOUND, ""))
        self.assertIn("not found: go.mod", err)

    def test_a_missing_file_exits_five(self) -> None:
        status, out, err = self.fails("read", "nope.go")
        self.assertEqual((status, out), (EXIT_SOURCE_READ_ERROR, ""))
        self.assertIn("no such file: nope.go", err)
        self.assertIn(f"expected an indexed .go file under {self.root}", err)

    def test_a_directory_is_not_a_file(self) -> None:
        status, out, err = self.fails("read", "internal/store/")
        self.assertEqual((status, out), (EXIT_SOURCE_READ_ERROR, ""))
        self.assertIn("no such file: internal/store", err)

    def test_a_colon_in_the_target_still_means_a_path(self) -> None:
        status, out, err = self.fails("read", "cmd:thing.go")
        self.assertEqual((status, out), (EXIT_SOURCE_READ_ERROR, ""))
        self.assertIn("no such file: cmd:thing.go", err)

    def test_traversal_is_refused_even_when_it_resolves_inside(self) -> None:
        for target in ("../outside.go", "internal/../main.go"):
            with self.subTest(target=target):
                status, out, err = self.fails("read", target)
                self.assertEqual((status, out), (EXIT_SECURITY, ""))
                self.assertIn(f"path traversal is not allowed: {target}", err)
                self.assertIn("only files inside the project root can be read", err)

    def test_an_absolute_path_is_refused(self) -> None:
        status, out, err = self.fails("read", "/etc/passwd.go")
        self.assertEqual((status, out), (EXIT_SECURITY, ""))
        self.assertIn("absolute paths are not allowed: /etc/passwd.go", err)
        self.assertIn("pass a path relative to the project root", err)

    def test_a_symlink_out_of_the_tree_is_refused(self) -> None:
        outside = os.path.join(self._tmp.name, "outside.go")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("package outside\n")
        os.symlink(outside, os.path.join(self.root, "link.go"))
        status, out, err = self.fails("read", "link.go")
        self.assertEqual((status, out), (EXIT_SECURITY, ""))
        self.assertIn("link.go resolves outside the project root", err)
        self.assertIn(outside, err)
        self.assertIn("the file is a symlink pointing out of the tree", err)

    def test_a_symlink_inside_the_tree_is_followed(self) -> None:
        os.symlink(
            os.path.join(self.root, "internal", "store", "store.go"),
            os.path.join(self.root, "inside.go"),
        )
        out = self.ok("read", "inside.go")
        self.assertIn("file: inside.go  (lines 1-69 of 69)", out)
        self.assertIn("49\tfunc (m *memory) Get(", out)

    def test_a_dot_prefixed_path_reads_the_same_file(self) -> None:
        self.assertEqual(self.ok("read", "./main.go"), self.ok("read", "main.go"))

    def test_a_suffix_and_start_together_are_a_usage_error(self) -> None:
        status, out, err = self.fails("read", "internal/store/store.go:12-20", "--start", "5")
        self.assertEqual((status, out), (EXIT_USAGE, ""))
        self.assertIn("pass the line range once: drop either the suffix in", err)
        self.assertIn("'internal/store/store.go:12-20'", err)

    def test_a_reversed_range_is_a_usage_error(self) -> None:
        status, out, err = self.fails("read", "main.go", "--start", "5", "--end", "4")
        self.assertEqual((status, out), (EXIT_USAGE, ""))
        self.assertIn("error: --end (4) is before --start (5)", err)

    def test_line_zero_is_a_usage_error(self) -> None:
        status, out, err = self.fails("read", "main.go", "--start", "0")
        self.assertEqual((status, out), (EXIT_USAGE, ""))
        self.assertIn("--start is 1-based and must be >= 1", err)

    def test_json_document_of_a_whole_file(self) -> None:
        status, payload = self.query_json("read", "main.go")
        self.assertEqual(status, EXIT_OK)
        self.assertIsNone(payload["symbol"])
        self.assertFalse(payload["index_parse_error"])
        self.assertEqual(payload["file"], "main.go")
        self.assertEqual(payload["path"], os.path.join(self.root, "main.go"))
        self.assertEqual(
            (payload["start"], payload["end"], payload["total_lines"]), (1, 26, 26)
        )
        self.assertFalse(payload["truncated"])
        self.assertEqual(len(payload["lines"]), 26)
        self.assertEqual(
            payload["lines"][0],
            {"number": 1, "text": "// Command demo stores and reads one answer."},
        )
        self.assertEqual(
            payload["lines"][11]["text"], "\tif err := run(context.Background()); err != nil {"
        )

    def test_json_records_truncation(self) -> None:
        status, payload = self.query_json(
            "read", "internal/store/store.go", "--limit", "3"
        )
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(payload["truncated"])
        self.assertEqual([line["number"] for line in payload["lines"]], [1, 2, 3])
        self.assertEqual(payload["end"], 3)

    def test_json_names_the_symbol_a_range_came_from(self) -> None:
        status, payload = self.query_json("read", "memory.Get")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            payload["symbol"], "method:example.com/demo/internal/store:memory.Get"
        )
        self.assertEqual((payload["start"], payload["end"]), (49, 55))
        self.assertEqual(
            payload["lines"][0],
            {
                "number": 49,
                "text": "func (m *memory) Get(ctx context.Context, id string) (Item, error) {",
            },
        )

    def test_json_flags_a_file_with_an_indexed_parse_error(self) -> None:
        status, payload = self.query_json("read", "broken/broken.go")
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(payload["index_parse_error"])
        self.assertEqual(payload["lines"][3]["text"], 'var BrokenValue = "unterminated')

    def test_json_of_an_empty_file_has_no_lines(self) -> None:
        self.write_project_file("empty.go", "")
        status, payload = self.query_json("read", "empty.go")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual((payload["start"], payload["end"]), (1, 0))
        self.assertEqual(
            (payload["total_lines"], payload["lines"], payload["truncated"]),
            (0, [], False),
        )


class DoctorTest(QueryTestCase):
    """``doctor``: is this index worth trusting, and why not?"""

    def test_a_clean_index_is_usable(self) -> None:
        status, out, err = self.query("doctor")
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn(f"index: {self.index_dir}", out)
        self.assertIn("checks (9):", out)
        self.assertIn("  ok    index directory", out)
        self.assertIn("  ok    required files             4 expected, 0 missing", out)
        self.assertIn("10 files, 0 malformed line(s)", out)
        self.assertIn("  0 failing, 1 warning(s): the index is usable", out)
        # The only warning is the two fixture files that do not parse.
        self.assertIn(
            "  warn  go parse errors            2 file(s) were indexed only partially", out
        )
        self.assertIn("broken/broken.go", out)
        self.assertIn("orphan/nopackage.go", out)

    def test_doctor_never_needs_the_index_to_exist(self) -> None:
        empty = os.path.join(self._tmp.name, "nothing-here")
        os.makedirs(empty, exist_ok=True)
        status, out, err = run_query(["--root", empty, "doctor"])
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn(f"index: {os.path.join(empty, '.ai-index')}", out)
        self.assertIn("  fail  index directory            missing:", out)
        self.assertIn("1 failing, 0 warning(s)", out)
        self.assertIn(f"hint: rebuild with python3 aindex-make {empty}", err)

    def test_limit_caps_the_evidence_shown(self) -> None:
        out = self.ok("doctor", "--limit", "1")
        self.assertIn("broken/broken.go", out)
        self.assertIn("        ... and 1 more", out)
        self.assertNotIn("orphan/nopackage.go", out)

    def test_json_document(self) -> None:
        status, payload = self.query_json("doctor")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["index_dir"], self.index_dir)
        self.assertEqual(payload["project_root"], self.root)
        self.assertEqual(
            (payload["failures"], payload["warnings"], payload["problems"]), (0, 1, [])
        )
        self.assertEqual(
            [check["name"] for check in payload["checks"]],
            [
                "index directory",
                "required files",
                "readable jsonl",
                "unique ids",
                "stats.json totals",
                "call edges",
                "go parse errors",
                "source files present",
                "freshness",
            ],
        )
        parse_errors = payload["checks"][6]
        self.assertEqual(parse_errors["status"], "warn")
        self.assertEqual(
            parse_errors["evidence"], ["broken/broken.go", "orphan/nopackage.go"]
        )

    def test_a_malformed_index_line_fails_the_readability_check(self) -> None:
        line = len(self.read_index_jsonl("symbols.jsonl")) + 1
        self.append_index_line("symbols.jsonl", "{not json")
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn("  fail  readable jsonl", out)
        self.assertIn("1 malformed line(s)", out)
        self.assertIn(f"{self.index_file('symbols.jsonl')}:{line}: invalid JSON", out)
        self.assertIn("1 failing, 1 warning(s)", out)
        self.assertIn("hint: rebuild with python3 aindex-make", err)

    def test_a_malformed_index_line_is_named_in_json(self) -> None:
        line = len(self.read_index_jsonl("symbols.jsonl")) + 1
        self.append_index_line("symbols.jsonl", "{not json")
        status, payload = self.query_json("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertEqual(payload["failures"], 1)
        self.assertEqual(
            payload["problems"],
            [
                {
                    "file": self.index_file("symbols.jsonl"),
                    "line": line,
                    "error": (
                        "invalid JSON: Expecting property name enclosed in "
                        "double quotes"
                    ),
                }
            ],
        )
        self.assertEqual(payload["checks"][2]["name"], "readable jsonl")
        self.assertEqual(payload["checks"][2]["status"], "fail")

    def test_a_duplicated_id_fails_the_unique_id_check(self) -> None:
        records = self.read_index_jsonl("symbols.jsonl")
        self.append_index_line("symbols.jsonl", json.dumps(records[0]))
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn("  fail  unique ids                 1 duplicated id(s)", out)
        self.assertIn("        package:example.com/demo:main", out)
        self.assertIn("1 failing, 1 warning(s)", out)

    def test_a_missing_required_file_is_a_failure(self) -> None:
        os.remove(self.index_file("files.jsonl"))
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn("  fail  required files             4 expected, 1 missing", out)
        self.assertIn("        files.jsonl: missing", out)
        self.assertIn("  warn  stats.json totals", out)
        self.assertIn("stats says 10 files, 0 found", out)

    def test_a_missing_stats_file_is_a_failure_but_totals_are_derived(self) -> None:
        os.remove(self.index_file("stats.json"))
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn("        stats.json: missing", out)
        self.assertIn("  warn  stats.json                 missing or unreadable", out)
        self.assertIn("totals were derived from symbols.jsonl", out)
        self.assertIn(
            "  ok    freshness                  no stats.json to date the index from", out
        )

    def test_a_source_file_that_vanished_is_a_failure(self) -> None:
        os.remove(os.path.join(self.root, "main.go"))
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn(
            "  fail  source files present       1 indexed file(s) are gone from disk", out
        )
        self.assertIn("        main.go", out)

    def test_a_call_to_an_id_that_is_gone_is_only_a_warning(self) -> None:
        self.edit_index_jsonl("symbols.jsonl", _point_main_at_a_ghost)
        status, out, err = self.query("doctor")
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("  warn  call edges                 1 call/caller edge(s) point at ids", out)
        self.assertIn(f"        func:example.com/demo:main -> {GHOST_CALL} (calls)", out)
        self.assertIn("0 failing, 2 warning(s): the index is usable", out)

    def test_an_unreadable_index_exits_four(self) -> None:
        path = self.index_file("symbols.jsonl")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        try:
            with open(path, "rb"):
                pass
        except OSError:
            pass
        else:  # pragma: no cover - root ignores file permissions
            self.skipTest("this user can read a chmod 000 file")
        status, out, err = self.query("packages")
        self.assertEqual((status, out), (EXIT_INVALID_INDEX, ""))
        self.assertIn(f"cannot read {path}", err)
        self.assertIn("the index is unreadable", err)


class BrokenIndexTest(QueryTestCase):
    """A damaged index: ``doctor`` complains, a query still answers.

    Read-only queries are tolerant on purpose -- one bad line must not hide the
    other 33 records, and a missing ``files.jsonl`` only costs the file counts.
    ``doctor`` is the command that has to be strict about the same damage.
    """

    def test_a_malformed_line_does_not_stop_a_query(self) -> None:
        self.append_index_line("symbols.jsonl", "{not json")
        out = self.ok("overview")
        self.assertIn(
            "health:  stats.json; 2 go files with parse errors; "
            "1 malformed index line",
            out,
        )
        self.assertIn("totals:  6 packages, 10 files, 28 symbols", out)
        status, payload = self.query_json("search", "New")
        self.assertEqual(
            (status, payload["total"], len(payload["results"])), (EXIT_OK, 5, 5)
        )
        status, payload = self.query_json("symbol", "memory.Get")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            payload["symbol"]["id"], "method:example.com/demo/internal/store:memory.Get"
        )
        self.assertEqual(self.query("callers", "New")[0], EXIT_OK)
        self.assertIn("file: main.go  (lines 1-26 of 26)", self.ok("read", "main.go"))

    def test_a_malformed_line_is_listed_in_the_overview_document(self) -> None:
        line = len(self.read_index_jsonl("symbols.jsonl")) + 1
        self.append_index_line("symbols.jsonl", "{not json")
        status, payload = self.query_json("overview")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            payload["problems"],
            [
                {
                    "file": self.index_file("symbols.jsonl"),
                    "line": line,
                    "error": (
                        "invalid JSON: Expecting property name enclosed in "
                        "double quotes"
                    ),
                }
            ],
        )

    def test_a_missing_files_jsonl_only_costs_the_file_counts(self) -> None:
        os.remove(self.index_file("files.jsonl"))
        out = self.ok("packages")
        # No file records, so no group can be recognised as clause-less.
        self.assertIn("packages (6):", out)
        self.assertNotIn("without a package clause", out)
        self.assertIn(
            "  (root)          main        0 files     3 symbols  "
            "example.com/demo",
            out,
        )
        self.assertIn(
            "health:  stats.json; 2 go files with parse errors; index clean",
            self.ok("overview"),
        )
        status, payload = self.query_json("overview")
        self.assertEqual(status, EXIT_OK)
        self.assertFalse(payload["stats_missing"])
        self.assertEqual(payload["stats"]["files"], 10)
        # The parse-error note comes from the file record that is now gone.
        status, payload = self.query_json("read", "broken/broken.go")
        self.assertEqual(status, EXIT_OK)
        self.assertFalse(payload["index_parse_error"])

    def test_files_jsonl_that_is_a_directory_counts_as_missing(self) -> None:
        os.remove(self.index_file("files.jsonl"))
        os.makedirs(self.index_file("files.jsonl"))
        self.assertIn(
            "  (root)          main        0 files     3 symbols  "
            "example.com/demo",
            self.ok("packages"),
        )
        status, out, err = self.query("doctor")
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertIn("        files.jsonl: missing", out)
        self.assertIn("34 symbol records (28 declarations, 6 package), 0 files", out)
        self.assertIn("stats says 10 files, 0 found", out)

    def test_a_missing_stats_json_is_derived_rather_than_fatal(self) -> None:
        os.remove(self.index_file("stats.json"))
        out = self.ok("overview")
        self.assertIn("generated: unknown (unknown ago)", out)
        self.assertIn("module:  (unknown)", out)
        # Totals are rebuilt from the records that are still there.
        self.assertIn("totals:  6 packages, 10 files, 28 symbols", out)
        self.assertIn(
            "health:  stats.json MISSING (values derived); "
            "2 go files with parse errors; index clean",
            out,
        )
        self.assertIn("packages (7, 1 without a package clause):", out)
        self.assertIn("entry points (9 of 9 exported declarations):", out)
        status, payload = self.query_json("overview")
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(payload["stats_missing"])

    def test_a_duplicated_id_does_not_confuse_a_query(self) -> None:
        records = self.read_index_jsonl("symbols.jsonl")
        self.append_index_line("symbols.jsonl", json.dumps(records[0]))
        self.assertIn("packages (7, 1 without a package clause):", self.ok("packages"))
        status, payload = self.query_json("symbol", "package:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["symbol"]["id"], "package:example.com/demo:main")
        self.assertEqual(self.query("search", "main")[0], EXIT_OK)
        self.assertIn(
            " 1\t// Command demo stores and reads one answer.",
            self.ok("read", "main.go"),
        )


class JsonContractTest(QueryTestCase):
    """``--json``: one document for an agent to parse, and nothing else.

    The contract the README promises: with ``--json`` stdout carries exactly one
    JSON document, so a caller can pipe it straight into a parser.  A reference
    that does not resolve is still a *result* (``found: false`` plus candidates,
    exit code 3); every other failure stays text on stderr and leaves stdout
    empty.
    """

    def test_every_command_prints_one_document(self) -> None:
        documents = {
            ("overview",): {
                "project_root",
                "index_dir",
                "explicit_index",
                "generated_at",
                "index_age_seconds",
                "module_path",
                "go_version",
                "stats",
                "stats_missing",
                "problems",
                "packages",
                "entry_points",
            },
            ("packages",): {"count", "packages", "pages"},
            ("package", "store"): {
                "package",
                "package_doc",
                "package_page",
                "files",
                "symbols",
            },
            ("search", "New"): {"query", "filters", "limit", "total", "results"},
            ("symbol", "memory.Get"): {"symbol", "calls", "callers", "dangling_calls"},
            ("callers", "New"): {"query", "symbol", "count", "callers"},
            ("callees", "New"): {
                "query",
                "symbol",
                "count",
                "callees",
                "unresolved_calls",
                "unresolved_count",
                "dangling_calls",
                "dangling_count",
            },
            ("read", "main.go"): {
                "file",
                "path",
                "symbol",
                "start",
                "end",
                "total_lines",
                "lines",
                "truncated",
                "index_parse_error",
            },
            ("doctor",): {
                "index_dir",
                "project_root",
                "checks",
                "failures",
                "warnings",
                "problems",
            },
        }
        for argv, keys in documents.items():
            with self.subTest(argv=argv):
                status, payload = self.query_json(*argv)
                self.assertEqual(status, EXIT_OK)
                self.assertEqual(set(payload), keys)

    def test_the_flag_may_follow_the_command(self) -> None:
        """``search New --json`` is the same request as ``--json search New``."""
        status, out, err = self.query("search", "New", "--json")
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertEqual(out, self.query("search", "New", json_mode=True)[1])
        self.assertEqual(self.query_json("search", "New")[1]["total"], 5)

    def test_a_reference_that_does_not_resolve_is_a_document(self) -> None:
        status, payload = self.query_json("symbol", "nope.Nope")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertEqual(payload["query"], "nope.Nope")
        self.assertFalse(payload["found"])
        self.assertFalse(payload["ambiguous"])
        self.assertEqual(payload["candidates"], [])
        status, payload = self.query_json("read", "nope.Nope")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertFalse(payload["found"])

    def test_an_unknown_caller_is_a_document_too(self) -> None:
        status, payload = self.query_json("callers", "Nope")
        self.assertEqual(status, EXIT_SYMBOL_NOT_FOUND)
        self.assertEqual((payload["found"], payload["candidates"]), (False, []))

    def test_a_hard_failure_leaves_stdout_empty(self) -> None:
        for argv, code in (
            (("package", "nope"), EXIT_SYMBOL_NOT_FOUND),
            (("read", os.path.join("..", "outside.go")), EXIT_SECURITY),
            (("read", "missing-dir/missing.go"), EXIT_SOURCE_READ_ERROR),
        ):
            with self.subTest(argv=argv):
                status, out, err = self.query(*argv, json_mode=True)
                self.assertEqual((status, out), (code, ""))
                self.assertIn("error: ", err)

    def test_a_failing_doctor_still_prints_a_document(self) -> None:
        empty = os.path.join(self._tmp.name, "no-index-here")
        os.makedirs(empty, exist_ok=True)
        status, out, err = run_query(["--json", "--root", empty, "doctor"])
        self.assertEqual(status, EXIT_INVALID_INDEX)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["failures"], 1)
        self.assertEqual(payload["checks"][0]["name"], "index directory")
        self.assertEqual(payload["checks"][0]["status"], "fail")

    def test_text_and_json_agree_on_the_numbers(self) -> None:
        text = self.ok("search", "New")
        self.assertIn("  5 matches, showing 5", text)
        self.assertIn("    1. 0.9800  func:example.com/demo/internal/store:New", text)
        status, payload = self.query_json("search", "New")
        # ``limit`` is the cap that was applied, not the number of matches.
        self.assertEqual((status, payload["total"], payload["limit"]), (EXIT_OK, 5, 10))
        self.assertEqual(len(payload["results"]), 5)
        self.assertEqual(
            payload["results"][0]["id"], "func:example.com/demo/internal/store:New"
        )

        calls = self.ok("callees", "func:example.com/demo:main")
        self.assertIn("    func:example.com/demo:run", calls)
        status, payload = self.query_json("callees", "func:example.com/demo:main")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(
            [target["id"] for target in payload["callees"]],
            ["func:example.com/demo:run"],
        )
        self.assertIn("    context.Background", calls)
        self.assertEqual(
            payload["unresolved_calls"], ["context.Background", "fmt.Println"]
        )

        listing = self.ok("packages")
        self.assertIn("packages (7, 1 without a package clause):", listing)
        status, payload = self.query_json("packages")
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(payload["count"], 7)


class EntryPointTest(QueryTestCase):
    """The two documented ways to start the CLI, run as real subprocesses.

    Every other test here drives :func:`aindex_query.cli.main` in process; these
    two are what proves ``./aindex-query`` and ``python3 -m aindex_query`` work
    straight from a checkout, which is how the README tells a caller to start.
    """

    def run_cli(self, *command: str) -> subprocess.CompletedProcess[str]:
        """Run ``command`` from the repository root; both streams are captured."""
        return subprocess.run(
            list(command),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_script_runs_a_command_against_an_index(self) -> None:
        completed = self.run_cli(LAUNCHER, "--root", self.root, "package", "store")
        self.assertEqual(completed.returncode, EXIT_OK, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertIn(
            "package store  (example.com/demo/internal/store)", completed.stdout
        )

    def test_python_m_aindex_query_prints_one_json_document(self) -> None:
        completed = self.run_cli(
            sys.executable,
            "-m",
            "aindex_query",
            "--json",
            "--root",
            self.root,
            "overview",
        )
        self.assertEqual(completed.returncode, EXIT_OK, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout)["project_root"], self.root)

