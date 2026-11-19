"""Project discovery: find the Go files worth indexing.

The walk is deterministic (sorted), never follows symlinks and honours:

* :data:`aindex.constants.DEFAULT_IGNORED_DIRS` -- build output, VCS metadata,
  ``vendor``, ``testdata`` ...;
* every directory whose name starts with a dot (``.git``, ``.idea`` and the
  index output directory itself);
* the project's root ``.gitignore`` (a small, dependency-free matcher: ``*``,
  ``?``, ``[...]``, ``**``, ``!`` negation, anchored and directory patterns).

The module path from ``go.mod`` is prefixed to every package path, so the paths
in the index are the *import paths* used by Go itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .constants import (
    DEFAULT_IGNORED_DIRS,
    GITIGNORE_FILENAME,
    GO_MOD_FILENAME,
)
from .model import GoModule

#: ``module example.com/m`` (optionally quoted, as in the pre-1.12 syntax).
_MODULE_RE = re.compile(r'^\s*module\s+(?:"([^"]+)"|(\S+))\s*(?://.*)?$', re.MULTILINE)
_GO_VERSION_RE = re.compile(r"^\s*go\s+(\S+)\s*(?://.*)?$", re.MULTILINE)


@dataclass(slots=True)
class GoFile:
    """A Go file on disk, with the paths used throughout the index."""

    #: absolute path on disk
    path: str
    #: path relative to the project root, always with ``/`` separators
    rel_path: str
    #: directory of ``rel_path`` relative to the root (``""`` for the root)
    directory: str
    #: file name including ``.go``
    name: str

    @property
    def is_test_file(self) -> bool:
        return self.name.endswith("_test.go")


@dataclass(slots=True)
class DiscoveryResult:
    """Everything the pipeline needs to start parsing."""

    root: str
    module: GoModule
    files: list[GoFile] = field(default_factory=list)
    #: number of *files* rejected by the ignore rules; files inside a pruned
    #: directory are never visited and therefore not counted
    ignored_files: int = 0


def read_go_module(root: str) -> GoModule:
    """Read ``go.mod``; returns empty fields when there is none."""
    path = os.path.join(root, GO_MOD_FILENAME)
    module = GoModule(path=path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return module
    match = _MODULE_RE.search(text)
    if match:
        module.module_path = (match.group(1) or match.group(2) or "").strip()
    version = _GO_VERSION_RE.search(text)
    if version:
        module.go_version = version.group(1).strip()
    return module


def package_path_for(module_path: str, rel_dir: str, suffix: str = "") -> str:
    """Import path of the package in ``rel_dir``.

    Without a ``go.mod`` the directory itself is used (``.``, ``./sub``), which
    is still unique and readable.
    """
    if module_path:
        base = f"{module_path}/{rel_dir}" if rel_dir else module_path
    else:
        base = f"./{rel_dir}" if rel_dir else "."
    return base + suffix



def _translate_glob(pattern: str) -> str:
    """Translate a gitignore glob into a regular expression body.

    Supports ``*`` (no separator), ``?``, ``[...]`` classes and ``**``
    (any number of path segments, including none).
    """
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                index += 2
                if pattern.startswith("/", index):
                    index += 1
                    out.append("(?:.*/)?")  # leading or middle ``**/``
                else:
                    out.append(".*")  # trailing ``**``
            else:
                index += 1
                out.append("[^/]*")
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = pattern.find("]", index + 1)
            if close < 0:
                out.append(re.escape(char))
                index += 1
                continue
            body = pattern[index + 1 : close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    return "".join(out)


@dataclass(slots=True, frozen=True)
class IgnoreRule:
    """One compiled line of a ``.gitignore`` file."""

    regex: re.Pattern[str]
    negated: bool
    dir_only: bool


def _compile_rule(line: str) -> IgnoreRule | None:
    """Compile one ``.gitignore`` line; ``None`` for blanks and comments."""
    text = line.rstrip("\n").rstrip()
    if not text or text.startswith("#"):
        return None
    negated = text.startswith("!")
    if negated:
        text = text[1:].lstrip()
    if not text:
        return None
    dir_only = text.endswith("/")
    if dir_only:
        text = text.rstrip("/")
    anchored = text.startswith("/") or "/" in text
    text = text.lstrip("/")
    if not text:
        return None
    body = _translate_glob(text)
    # A pattern without a slash matches at any depth; the others are anchored.
    regex = body if anchored else "(?:.*/)?" + body
    try:
        return IgnoreRule(re.compile(regex + r"\Z"), negated, dir_only)
    except re.error:
        return None


@dataclass(slots=True)
class IgnoreMatcher:
    """Ordered ``.gitignore`` rules; the last matching rule wins."""

    rules: list[IgnoreRule] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.rules)

    def match(self, path: str, is_dir: bool = False) -> bool:
        """True when ``path`` (relative, ``/`` separated) is ignored.

        Every ancestor directory is tested too: a rule that ignores a directory
        ignores everything below it.
        """
        if not self.rules:
            return False
        parts = path.split("/")
        ignored = False
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            prefix_is_dir = is_dir if index == len(parts) else True
            for rule in self.rules:
                if rule.dir_only and not prefix_is_dir:
                    continue
                if rule.regex.fullmatch(prefix):
                    ignored = not rule.negated
        return ignored


def load_gitignore(root: str, filename: str = GITIGNORE_FILENAME) -> IgnoreMatcher:
    """Read the root ``.gitignore``; a missing or unreadable file yields no rules."""
    path = os.path.join(root, filename)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return IgnoreMatcher()
    rules = [rule for rule in (_compile_rule(line) for line in lines) if rule is not None]
    return IgnoreMatcher(rules)


def _skipped_dir(name: str, ignored_dirs: frozenset[str]) -> bool:
    """Directories never walked: known build/VCS names and any dot directory."""
    return name in ignored_dirs or name.startswith(".")


def discover(
    root: str,
    *,
    ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
    extra_ignores: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> DiscoveryResult:
    """Walk ``root`` and collect every Go file that should be indexed.

    ``extra_ignores`` are extra gitignore-style patterns (from ``--ignore``)
    applied on top of the project's own ``.gitignore``.
    """
    root = os.path.abspath(root)
    module = read_go_module(root)
    matcher = load_gitignore(root) if use_gitignore else IgnoreMatcher()
    for pattern in extra_ignores:
        rule = _compile_rule(pattern)
        if rule is not None:
            matcher.rules.append(rule)
    result = DiscoveryResult(root=root, module=module)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = _relative_dir(root, current)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _skipped_dir(name, ignored_dirs)
            and not matcher.match(_join(rel_dir, name), is_dir=True)
        )
        for filename in sorted(filenames):
            rel_path = _join(rel_dir, filename)
            if not filename.endswith(".go"):
                continue
            if matcher.match(rel_path):
                result.ignored_files += 1
                continue
            result.files.append(
                GoFile(
                    path=os.path.join(current, filename),
                    rel_path=rel_path,
                    directory=rel_dir,
                    name=filename,
                )
            )
    result.files.sort(key=lambda go_file: go_file.rel_path)
    return result


def _join(rel_dir: str, name: str) -> str:
    return f"{rel_dir}/{name}" if rel_dir else name


def _relative_dir(root: str, current: str) -> str:
    rel = os.path.relpath(current, root)
    if rel in (".", ""):
        return ""
    return rel.replace(os.sep, "/")

