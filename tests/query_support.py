"""Shared fixtures and helpers for the ``aindex-query`` tests.

The query CLI reads an index; rather than hand-writing ``.ai-index/`` fixtures,
the tests reuse the demo project of :mod:`tests.support` and index it for real,
so what the CLI reports is checked against what :mod:`aindex` actually produced.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from typing import Any, Callable

from aindex_query.cli import main
from tests.support import IndexTestCase


def run_query(argv: list[str]) -> tuple[int, str, str]:
    """Run one ``aindex-query`` command in-process and capture its output."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = main(argv)
        except SystemExit as stop:  # ``--help``/``--version`` and usage errors
            status = int(stop.code or 0)
    return status, out.getvalue(), err.getvalue()


class QueryTestCase(IndexTestCase):
    """The demo project of :mod:`tests.support`, indexed once per test."""

    def setUp(self) -> None:
        super().setUp()
        self.write_index()
        self.base: list[str] = ["--root", self.root]

    # -- running the CLI ---------------------------------------------------
    def query(self, *arguments: str, json_mode: bool = False) -> tuple[int, str, str]:
        """Run ``aindex-query`` against the fixture index."""
        argv = [*self.base]
        if json_mode:
            argv.append("--json")
        argv.extend(arguments)
        return run_query(argv)

    def query_json(self, *arguments: str) -> tuple[int, dict[str, Any]]:
        """Run a command with ``--json`` and parse the document it printed."""
        status, out, err = self.query(*arguments, json_mode=True)
        self.assertEqual(err, "", "JSON mode must keep stderr empty")
        self.assertTrue(out.strip(), "JSON mode must print one document")
        return status, json.loads(out)

    def ok(self, *arguments: str) -> str:
        """Run a command that must succeed and return its text output."""
        status, out, err = self.query(*arguments)
        self.assertEqual((status, err), (0, ""), out)
        return out

    def fails(self, *arguments: str) -> tuple[int, str, str]:
        """Run a command that must fail; returns exit code and both streams."""
        status, out, err = self.query(*arguments)
        self.assertNotEqual(status, 0, out or err)
        self.assertNotEqual(err, "")
        return status, out, err

    # -- paths -------------------------------------------------------------
    def index_file(self, *names: str) -> str:
        """A path inside the fixture's ``.ai-index/``."""
        return os.path.join(self.index_dir, *names)

    def read_index_jsonl(self, name: str) -> list[dict[str, Any]]:
        with open(self.index_file(name), "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    # -- damaging the fixture on purpose -----------------------------------
    def write_project_file(self, rel_path: str, text: str = "") -> str:
        """Create or replace a file in the demo project; returns its path."""
        path = os.path.join(self.root, *rel_path.split("/"))
        os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def edit_index_jsonl(
        self,
        name: str,
        transform: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> None:
        """Rewrite one index JSONL file record by record.

        ``transform`` returns the record to keep or ``None`` to drop it, which
        is how the tests build indexes that are stale, duplicated or dangling.
        """
        rewritten: list[dict[str, Any]] = []
        for record in self.read_index_jsonl(name):
            replacement = transform(dict(record))
            if replacement is not None:
                rewritten.append(replacement)
        with open(self.index_file(name), "w", encoding="utf-8") as handle:
            for record in rewritten:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_index_line(self, name: str, text: str) -> None:
        """Append a raw line to an index JSONL file (to corrupt it on purpose)."""
        with open(self.index_file(name), "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
