"""Discovery: which Go files are walked, which are ignored, and how they are named."""

from __future__ import annotations

import os
import unittest

from aindex.discovery import (
    IgnoreMatcher,
    _compile_rule,
    discover,
    package_path_for,
    read_go_module,
)
from aindex.pipeline import _directory_of
from tests.support import IGNORED_FILES, INDEXED_FILES, MODULE_PATH, IndexTestCase


def rel_paths(root: str, **kwargs: object) -> list[str]:
    return [go_file.rel_path for go_file in discover(root, **kwargs).files]


class PackagePathTest(unittest.TestCase):
    def test_module_path_is_prefixed(self) -> None:
        self.assertEqual(
            package_path_for(MODULE_PATH, "internal/store"),
            "example.com/demo/internal/store",
        )

    def test_root_directory_has_no_trailing_slash(self) -> None:
        self.assertEqual(package_path_for(MODULE_PATH, ""), MODULE_PATH)

    def test_test_suffix_is_appended(self) -> None:
        self.assertEqual(
            package_path_for(MODULE_PATH, "internal/api", "_test"),
            "example.com/demo/internal/api_test",
        )

    def test_without_go_mod_relative_paths_are_used(self) -> None:
        self.assertEqual(package_path_for("", ""), ".")
        self.assertEqual(package_path_for("", "pkg/sub"), "./pkg/sub")


class DirectoryOfTest(unittest.TestCase):
    def test_root_files_have_no_directory(self) -> None:
        self.assertEqual(_directory_of("main.go"), "")

    def test_nested_files_report_their_directory(self) -> None:
        self.assertEqual(_directory_of("internal/api/api.go"), "internal/api")

    def test_agrees_with_the_walk(self) -> None:
        for rel_path in INDEXED_FILES:
            with self.subTest(rel_path=rel_path):
                self.assertEqual(_directory_of(rel_path), os.path.dirname(rel_path))


class ReadGoModuleTest(IndexTestCase):
    def test_reads_module_and_go_version(self) -> None:
        module = read_go_module(self.root)
        self.assertEqual(module.module_path, MODULE_PATH)
        self.assertEqual(module.go_version, "1.21.5")
        self.assertEqual(module.path, os.path.join(self.root, "go.mod"))

    def test_missing_go_mod_is_not_an_error(self) -> None:
        module = read_go_module(os.path.join(self.root, "docs"))
        self.assertEqual(module.module_path, "")
        self.assertEqual(module.go_version, "")


class IgnoreMatcherTest(unittest.TestCase):
    def test_empty_matcher_ignores_nothing(self) -> None:
        matcher = IgnoreMatcher()
        self.assertFalse(matcher)
        self.assertFalse(matcher.match("anything/at/all.go"))

    def test_anchored_directory_rule_covers_children(self) -> None:
        matcher = IgnoreMatcher(rules=[_compile_rule("/tmp/")])
        self.assertTrue(matcher.match("tmp/cache.go"))
        self.assertFalse(matcher.match("pkg/tmp.go"))

    def test_deep_glob_matches_any_depth(self) -> None:
        matcher = IgnoreMatcher(rules=[_compile_rule("**/gen/*.go")])
        self.assertTrue(matcher.match("gen/db.go"))
        self.assertTrue(matcher.match("a/b/gen/db.go"))

    def test_rules_that_do_not_match_return_false(self) -> None:
        matcher = IgnoreMatcher(rules=[_compile_rule("*.gen.go")])
        self.assertFalse(matcher.match("gen/keep.txt"))
        self.assertTrue(matcher.match("gen/db.gen.go"))

    def test_last_matching_rule_wins(self) -> None:
        matcher = IgnoreMatcher(rules=[_compile_rule("*.gen.go"), _compile_rule("!keep.gen.go")])
        self.assertTrue(matcher.match("gen/db.gen.go"))
        self.assertFalse(matcher.match("keep.gen.go"))


class DiscoveryTest(IndexTestCase):
    def test_indexes_exactly_the_expected_files(self) -> None:
        self.assertEqual(rel_paths(self.root), list(INDEXED_FILES))

    def test_files_are_sorted_by_relative_path(self) -> None:
        paths = rel_paths(self.root)
        self.assertEqual(paths, sorted(paths))

    def test_test_files_are_flagged(self) -> None:
        found = {f.rel_path: f.is_test_file for f in discover(self.root).files}
        self.assertTrue(found["internal/store/store_test.go"])
        self.assertFalse(found["internal/store/store.go"])

    def test_directories_and_relative_names_are_recorded(self) -> None:
        by_path = {f.rel_path: f for f in discover(self.root).files}
        nested = by_path["internal/api/api.go"]
        self.assertEqual(nested.directory, "internal/api")
        self.assertEqual(nested.name, "api.go")
        self.assertTrue(nested.path.endswith(os.path.join("internal", "api", "api.go")))
        self.assertFalse(os.path.isabs(nested.rel_path))
        root_file = by_path["main.go"]
        self.assertEqual(root_file.directory, "")
        self.assertEqual(root_file.name, "main.go")

    def test_ignored_files_are_absent(self) -> None:
        indexed = set(rel_paths(self.root))
        for rel_path in IGNORED_FILES:
            with self.subTest(rel_path=rel_path):
                self.assertNotIn(rel_path, indexed)

    def test_negated_gitignore_rule_keeps_a_file(self) -> None:
        # ``*.gen.go`` is ignored, but ``!keep.gen.go`` wins for that one file.
        indexed = rel_paths(self.root)
        self.assertIn("keep.gen.go", indexed)
        self.assertNotIn("gen/db.gen.go", indexed)

    def test_ignored_counter_counts_visited_files_only(self) -> None:
        # Only ``gen/db.gen.go`` is visited and rejected; the other ignored files
        # live in pruned directories and are therefore never visited.
        self.assertEqual(discover(self.root).ignored_files, 1)

    def test_no_gitignore_includes_ignored_files_again(self) -> None:
        paths = rel_paths(self.root, use_gitignore=False)
        self.assertIn("gen/db.gen.go", paths)
        self.assertIn("tmp/cache.go", paths)
        # Built-in directory rules still apply.
        self.assertNotIn("vendor/dep/dep.go", paths)
        self.assertNotIn(".hidden/secret.go", paths)
        self.assertNotIn("testdata/sample.go", paths)

    def test_extra_ignores_prune_directories(self) -> None:
        paths = rel_paths(self.root, extra_ignores=("broken",))
        self.assertNotIn("broken/broken.go", paths)
        self.assertIn("main.go", paths)

    def test_extra_ignores_can_match_a_single_file(self) -> None:
        paths = rel_paths(self.root, extra_ignores=("platform/mode_windows.go",))
        self.assertNotIn("platform/mode_windows.go", paths)
        self.assertIn("platform/mode_linux.go", paths)

    def test_ignored_dirs_can_be_replaced(self) -> None:
        paths = rel_paths(self.root, ignored_dirs=frozenset({"docs"}))
        self.assertIn("vendor/dep/dep.go", paths)
        self.assertIn("testdata/sample.go", paths)
        # Dot directories are always skipped, whatever ``ignored_dirs`` says.
        self.assertNotIn(".hidden/secret.go", paths)


if __name__ == "__main__":
    unittest.main()
