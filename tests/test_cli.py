"""The command line interface: argument parsing, the report and the exit codes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from aindex import __version__, cli
from aindex.cli import EXIT_ERROR, EXIT_OK, build_parser, main, options_from_args, report
from aindex.constants import DEFAULT_MAX_DOC_CHARS, DEFAULT_OUTPUT_DIR, PROJECT_FILENAME
from aindex.pipeline import IndexOptions, build_index
from tests.support import (
    GO_MOD,
    MAIN_GO,
    IndexTestCase,
    read_json,
    read_jsonl,
    read_text,
    write_tree,
)

#: A project without parse errors, used to check ``--fail-on-parse-error``.
CLEAN_FILES = {"go.mod": GO_MOD, "main.go": MAIN_GO}


class RunCli:
    """Call :func:`aindex.cli.main` and capture what it printed."""

    def invoke(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(argv)
        return status, out.getvalue(), err.getvalue()


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_prog_name_and_defaults(self) -> None:
        args = self.parser.parse_args([])
        self.assertEqual(self.parser.prog, "aindex-make")
        self.assertEqual(args.root, ".")
        self.assertEqual(args.out, DEFAULT_OUTPUT_DIR)
        self.assertEqual(args.ignore, [])
        self.assertEqual(args.max_doc_chars, DEFAULT_MAX_DOC_CHARS)
        self.assertFalse(args.clean)
        self.assertFalse(args.quiet)
        self.assertFalse(args.json)
        self.assertFalse(args.no_gitignore)
        self.assertFalse(args.no_tests)
        self.assertFalse(args.fail_on_parse_error)

    def test_root_and_output_directory(self) -> None:
        args = self.parser.parse_args(["some/project", "-o", "out"])
        self.assertEqual(args.root, "some/project")
        self.assertEqual(args.out, "out")
        self.assertEqual(self.parser.parse_args(["x", "--out", "y"]).out, "y")

    def test_ignore_can_be_repeated(self) -> None:
        args = self.parser.parse_args(["--ignore", "a/", "--ignore", "*.pb.go"])
        self.assertEqual(args.ignore, ["a/", "*.pb.go"])

    def test_boolean_flags(self) -> None:
        args = self.parser.parse_args(
            ["--clean", "--quiet", "--json", "--no-gitignore", "--no-tests"]
        )
        self.assertTrue(args.clean)
        self.assertTrue(args.quiet)
        self.assertTrue(args.json)
        self.assertTrue(args.no_gitignore)
        self.assertTrue(args.no_tests)

    def test_max_doc_chars_is_an_integer(self) -> None:
        self.assertEqual(self.parser.parse_args(["--max-doc-chars", "40"]).max_doc_chars, 40)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.parser.parse_args(["--max-doc-chars", "many"])

    def test_unknown_arguments_are_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.parser.parse_args(["--nope"])

    def test_version_flag_prints_the_version(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(out):
            self.parser.parse_args(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"aindex-make {__version__}")

    def test_help_documents_the_output_files(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            self.parser.parse_args(["--help"])
        self.assertIn("symbols.jsonl", out.getvalue())


class OptionsFromArgsTest(unittest.TestCase):
    def test_defaults_match_the_pipeline_defaults(self) -> None:
        options = options_from_args(build_parser().parse_args([]))
        self.assertIsInstance(options, IndexOptions)
        self.assertEqual(options.root, ".")
        self.assertEqual(options.output_dir, DEFAULT_OUTPUT_DIR)
        self.assertEqual(options.max_doc_chars, DEFAULT_MAX_DOC_CHARS)
        self.assertEqual(options.extra_ignores, ())
        self.assertTrue(options.use_gitignore)
        self.assertTrue(options.include_tests)
        self.assertFalse(options.clean)

    def test_flags_are_translated(self) -> None:
        args = build_parser().parse_args(
            [
                "root",
                "--out",
                "out",
                "--ignore",
                "docs/",
                "--no-gitignore",
                "--no-tests",
                "--clean",
            ]
        )
        options = options_from_args(args)
        self.assertEqual(options.root, "root")
        self.assertEqual(options.output_dir, "out")
        self.assertEqual(options.extra_ignores, ("docs/",))
        self.assertFalse(options.use_gitignore)
        self.assertFalse(options.include_tests)
        self.assertTrue(options.clean)

    def test_a_negative_doc_limit_is_clamped_to_zero(self) -> None:
        args = build_parser().parse_args(["--max-doc-chars", "-5"])
        self.assertEqual(options_from_args(args).max_doc_chars, 0)


class ReportTest(IndexTestCase):
    """``report`` prints the summary of a finished run."""

    def setUp(self) -> None:
        super().setUp()
        self.result, self.written = self.write_index()
        self.options = IndexOptions(root=self.root)

    def printed(self, result=None, **kwargs) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report(result or self.result, self.options, **kwargs)
        return out.getvalue()

    def clean_result(self):
        """A project without parse errors, for the "nothing unreadable" branch."""
        root = write_tree(os.path.join(self._tmp.name, "clean"), CLEAN_FILES)
        return build_index(IndexOptions(root=root))

    def test_summary_lines(self) -> None:
        lines = self.printed(written=self.written).splitlines()
        self.assertEqual(lines[0], f"indexed {self.root} -> {self.index_dir}")
        self.assertEqual(lines[1], "  packages: 6")
        self.assertEqual(lines[2], "  files: 10")
        self.assertEqual(lines[3], "  symbols: 28")
        self.assertEqual(lines[4], "  calls: 21 (resolved 10, unresolved 11)")
        self.assertEqual(lines[5], "  parse errors: 2")
        self.assertTrue(lines[6].startswith("  duration: "))
        self.assertEqual(
            lines[7], f"  start here: {os.path.join(self.index_dir, PROJECT_FILENAME)}"
        )
        self.assertEqual(
            lines[8], "  unreadable files: broken/broken.go, orphan/nopackage.go"
        )
        self.assertEqual(len(lines), 9)

    def test_the_list_of_unreadable_files_can_be_cut_short(self) -> None:
        with mock.patch.object(cli, "MAX_REPORTED_BROKEN_FILES", 1):
            self.assertIn(
                "  unreadable files: broken/broken.go …", self.printed()
            )

    def test_a_clean_run_lists_no_unreadable_files(self) -> None:
        self.assertNotIn("unreadable", self.printed(result=self.clean_result()))

    def test_json_report(self) -> None:
        payload = json.loads(self.printed(written=self.written, as_json=True))
        self.assertEqual(payload["output_dir"], self.index_dir)
        self.assertEqual(payload["index_files"], self.written)
        self.assertEqual(payload["root"], self.root)
        self.assertEqual(payload["module_path"], "example.com/demo")
        self.assertEqual(payload["files"], 10)
        self.assertEqual(payload["symbols"], 28)
        self.assertEqual(payload["parse_errors"], 2)
        self.assertEqual(payload["backend"], "python")

    def test_json_report_without_written_paths(self) -> None:
        payload = json.loads(self.printed(as_json=True))
        self.assertEqual(payload["index_files"], [])

    def test_json_report_keeps_non_ascii_readable(self) -> None:
        self.result.stats.root = "/tmp/prüfung"
        self.assertIn("prüfung", self.printed(as_json=True))

    def test_report_ignores_the_written_paths_without_json(self) -> None:
        self.assertNotIn("symbols.jsonl", self.printed(written=self.written))



class MainTest(IndexTestCase, RunCli):
    """End-to-end: ``main`` writes the index and returns an exit status."""

    def test_happy_path(self) -> None:
        status, out, err = self.invoke([self.root])
        self.assertEqual(status, EXIT_OK)
        self.assertEqual(err, "")
        self.assertIn(f"indexed {self.root} -> {self.index_dir}", out)
        self.assertIn(f"start here: {os.path.join(self.index_dir, 'PROJECT.md')}", out)
        self.assertTrue(os.path.isfile(os.path.join(self.index_dir, "PROJECT.md")))
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual(stats["files"], 10)

    def test_the_root_argument_is_optional(self) -> None:
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.root)
        status, _, _ = self.invoke([])
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(
            os.path.isfile(os.path.join(self.root, DEFAULT_OUTPUT_DIR, "PROJECT.md"))
        )

    def test_argv_defaults_to_sys_argv(self) -> None:
        argv = ["aindex-make", self.root, "--quiet"]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(main(), EXIT_OK)
        self.assertTrue(os.path.isfile(os.path.join(self.index_dir, "stats.json")))

    def test_quiet_writes_the_index_but_prints_nothing(self) -> None:
        status, out, err = self.invoke([self.root, "--quiet"])
        self.assertEqual((status, out, err), (EXIT_OK, "", ""))
        self.assertEqual(sorted(os.listdir(self.index_dir))[0], "PROJECT.md")

    def test_json_output(self) -> None:
        status, out, _ = self.invoke([self.root, "--json"])
        self.assertEqual(status, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["output_dir"], self.index_dir)
        self.assertEqual(payload["files"], 10)
        self.assertEqual(len(payload["index_files"]), 10)
        self.assertEqual(
            payload["index_files"][-1],
            os.path.join(self.index_dir, "packages/platform.md"),
        )

    def test_custom_output_directory(self) -> None:
        status, _, _ = self.invoke([self.root, "-o", "build/ai", "--quiet"])
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "build/ai/stats.json")))
        self.assertFalse(os.path.isdir(self.index_dir))

    def test_no_tests_switch(self) -> None:
        self.invoke([self.root, "--no-tests", "--quiet"])
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual((stats["files"], stats["symbols"]), (8, 26))

    def test_ignore_switch(self) -> None:
        self.invoke([self.root, "--ignore", "platform/", "--quiet"])
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual(stats["files"], 8)
        self.assertNotIn(
            "platform", read_text(os.path.join(self.index_dir, "PROJECT.md"))
        )

    def test_no_gitignore_switch(self) -> None:
        self.invoke([self.root, "--no-gitignore", "--quiet"])
        stats = read_json(os.path.join(self.index_dir, "stats.json"))
        self.assertEqual((stats["files"], stats["ignored_files"]), (12, 0))

    def test_max_doc_chars_switch(self) -> None:
        self.invoke([self.root, "--max-doc-chars", "10", "--quiet"])
        records = read_jsonl(os.path.join(self.index_dir, "symbols.jsonl"))
        item = next(record for record in records if record["name"] == "Item")
        self.assertEqual(item["doc"], "Item is...")

    def test_clean_switch_removes_the_old_index(self) -> None:
        self.invoke([self.root, "--quiet"])
        stale = os.path.join(self.index_dir, "stale.md")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("old\n")
        self.invoke([self.root, "--quiet", "--clean"])
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(sorted(os.listdir(self.index_dir))[0], "PROJECT.md")

    def test_fail_on_parse_error_reports_and_fails(self) -> None:
        status, out, err = self.invoke([self.root, "--fail-on-parse-error"])
        self.assertEqual(status, EXIT_ERROR)
        self.assertIn("parse errors: 2", out)
        self.assertEqual(err, "failing because --fail-on-parse-error was given\n")

    def test_fail_on_parse_error_is_quiet_about_it_in_quiet_mode(self) -> None:
        status, out, err = self.invoke([self.root, "--fail-on-parse-error", "--quiet"])
        self.assertEqual((status, out, err), (EXIT_ERROR, "", ""))

    def test_an_invalid_output_directory_is_an_error(self) -> None:
        status, out, err = self.invoke([self.root, "-o", ".", "--quiet"])
        self.assertEqual(status, EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: refusing to use "))

    def test_version_and_help_exit_without_indexing(self) -> None:
        for flag in ("--version", "--help"):
            out = io.StringIO()
            with self.assertRaises(SystemExit) as caught:
                with contextlib.redirect_stdout(out):
                    main([flag])
            self.assertEqual(caught.exception.code, 0)
            self.assertFalse(os.path.isdir(self.index_dir))


class CleanProjectTest(IndexTestCase, RunCli):
    """A project without parse errors."""

    files = CLEAN_FILES

    def test_index_is_written(self) -> None:
        status, out, err = self.invoke([self.root])
        self.assertEqual((status, err), (EXIT_OK, ""))
        self.assertIn("  parse errors: 0", out)
        self.assertNotIn("unreadable", out)

    def test_fail_on_parse_error_succeeds(self) -> None:
        status, _, err = self.invoke([self.root, "--fail-on-parse-error"])
        self.assertEqual((status, err), (EXIT_OK, ""))


class EntryPointTest(IndexTestCase):
    """The two documented ways to start the tool, run as real subprocesses."""

    #: The checkout holding ``aindex-make`` and the ``aindex`` package.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run_python(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_script_indexes_a_project(self) -> None:
        completed = self.run_python("aindex-make", self.root, "--quiet")
        self.assertEqual(completed.returncode, EXIT_OK, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")
        self.assertTrue(os.path.isfile(os.path.join(self.index_dir, "stats.json")))

    def test_python_m_aindex_reports_the_version(self) -> None:
        completed = self.run_python("-m", "aindex", "--version")
        self.assertEqual(completed.returncode, EXIT_OK, completed.stderr)
        self.assertEqual(completed.stdout.strip(), f"aindex-make {__version__}")


if __name__ == "__main__":
    unittest.main()

