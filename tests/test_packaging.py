"""The packaging metadata: one distribution, two commands, no drift.

``pyproject.toml`` is what ``pipx`` / ``pip`` read before anything is
installed, so a typo in it is an installation bug rather than a cosmetic one.
Nothing here builds or installs: the file is parsed and checked against the
checkout, which keeps this test as dependency-free as the rest of the suite
(``tomllib`` is standard library on the 3.11+ the tool requires).
"""

from __future__ import annotations

import contextlib
import io
import os
import tomllib
import unittest
from typing import Any, Callable

from aindex import __version__ as indexer_version
from aindex import cli as indexer_cli
from aindex_query import __version__ as query_version
from aindex_query import cli as query_cli

#: The repository root: ``pyproject.toml``, ``LICENCE`` and both packages.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PYPROJECT = os.path.join(REPO_ROOT, "pyproject.toml")

#: The console scripts an installed distribution has to expose, and where each
#: one leads.  ``pipx install`` writes these names into ``~/.local/bin``, and
#: the CLI parses ``argv`` itself, so they are part of the public interface.
EXPECTED_SCRIPTS = {
    "aindex-make": "aindex.cli:main",
    "aindex-query": "aindex_query.cli:main",
}

#: The packages that make up the distribution, and the import names they keep.
EXPECTED_PACKAGES = ["aindex", "aindex_query"]


def read_pyproject() -> dict[str, Any]:
    """Parse ``pyproject.toml``; the file must exist and be valid TOML."""
    with open(PYPROJECT, "rb") as handle:
        return tomllib.load(handle)


def resolve(target: str) -> Callable[..., int]:
    """Resolve a ``module:attribute`` console-script target."""
    module_name, _, attribute = target.partition(":")
    module = __import__(module_name, fromlist=[attribute])
    return getattr(module, attribute)


def run_main(main: Callable[..., Any], argv: list[str]) -> tuple[int, str]:
    """Call a CLI ``main`` and report what it printed.

    ``--version`` leaves through ``SystemExit`` in ``aindex-make`` and through a
    return value in ``aindex-query``; both mean success, so normalise them.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            status = main(argv)
        except SystemExit as exit_code:  # argparse: --version and --help
            status = exit_code.code
    return int(status or 0), out.getvalue()


class MetadataTest(unittest.TestCase):
    """The ``[project]`` table describes this project and nothing else."""

    def setUp(self) -> None:
        self.pyproject = read_pyproject()
        self.project = self.pyproject["project"]

    def test_project_table(self) -> None:
        self.assertEqual(self.project["name"], "aindex-go-index")
        self.assertEqual(self.project["requires-python"], ">=3.11")
        self.assertEqual(self.project["dependencies"], [])
        self.assertEqual(self.project["readme"], "README.md")
        self.assertTrue(self.project["description"].strip())

    def test_license_and_its_file(self) -> None:
        self.assertEqual(self.project["license"], {"text": "MIT"})
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, "LICENCE")))
        self.assertIn(
            "License :: OSI Approved :: MIT License", self.project["classifiers"]
        )

    def test_build_backend_is_declared(self) -> None:
        build_system = self.pyproject["build-system"]
        self.assertEqual(build_system["build-backend"], "setuptools.build_meta")
        self.assertTrue(
            any(req.startswith("setuptools") for req in build_system["requires"]),
            build_system["requires"],
        )


class VersionTest(unittest.TestCase):
    """One version, read from ``aindex/__init__.py``, shared by both packages."""

    def setUp(self) -> None:
        self.pyproject = read_pyproject()

    def test_the_two_packages_agree(self) -> None:
        self.assertEqual(indexer_version, query_version)

    def test_pyproject_reads_the_indexer_package(self) -> None:
        dynamic = self.pyproject["tool"]["setuptools"]["dynamic"]
        self.assertEqual(dynamic["version"], {"attr": "aindex.__version__"})
        self.assertNotIn("version", self.pyproject["project"])

    def test_the_commands_report_the_installed_version(self) -> None:
        # The console-script targets, the names they print and the version
        # literals inside the two packages all have to line up.
        for target, name, version in (
            ("aindex.cli:main", "aindex-make", indexer_version),
            ("aindex_query.cli:main", "aindex-query", query_version),
        ):
            with self.subTest(target=target):
                status, stdout = run_main(resolve(target), ["--version"])
                self.assertEqual(status, 0)
                self.assertEqual(stdout.strip(), f"{name} {version}")


class ScriptTest(unittest.TestCase):
    """The console-script names equal the ones the CLIs print themselves."""

    def setUp(self) -> None:
        self.scripts = read_pyproject()["project"]["scripts"]

    def test_declared_scripts(self) -> None:
        self.assertEqual(self.scripts, EXPECTED_SCRIPTS)

    def test_targets_are_importable_callables(self) -> None:
        for target in self.scripts.values():
            self.assertTrue(callable(resolve(target)), target)

    def test_script_names_match_the_argument_parsers(self) -> None:
        indexer_prog = indexer_cli.build_parser().prog
        query_prog = query_cli.build_parser().prog
        self.assertEqual(indexer_prog, "aindex-make")
        self.assertEqual(query_prog, "aindex-query")
        self.assertEqual(sorted(self.scripts), sorted([indexer_prog, query_prog]))


class LayoutTest(unittest.TestCase):
    """Only the two packages ship; the checkout launchers stay in the checkout."""

    def setUp(self) -> None:
        self.tool = read_pyproject()["tool"]["setuptools"]

    def test_packages_are_listed_explicitly(self) -> None:
        self.assertEqual(self.tool["packages"], EXPECTED_PACKAGES)
        self.assertNotIn("tests", self.tool["packages"])

    def test_every_listed_package_is_present_in_the_checkout(self) -> None:
        for name in self.tool["packages"]:
            package_dir = os.path.join(REPO_ROOT, name)
            self.assertTrue(
                os.path.isfile(os.path.join(package_dir, "__init__.py")), package_dir
            )

    def test_the_launcher_scripts_are_not_inside_a_package(self) -> None:
        # The checkout keeps ``aindex-make`` / ``aindex-query`` for the entry
        # point tests; they must not shadow what the console scripts install.
        for launcher in ("aindex-make", "aindex-query"):
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, launcher)))
            for name in self.tool["packages"]:
                self.assertFalse(
                    os.path.exists(os.path.join(REPO_ROOT, name, launcher)), launcher
                )


if __name__ == "__main__":
    unittest.main()
