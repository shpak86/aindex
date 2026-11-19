"""Smoke test for aindex.discovery against a throwaway fixture tree."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from aindex.discovery import discover, load_gitignore, package_path_for, read_go_module

ROOT = "/tmp/aindex_disc/proj"


def check(label, actual, expected):
    if actual == expected:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label}\n     actual   {actual!r}\n     expected {expected!r}")
        return 1
    return 0


def main() -> int:
    failures = 0
    module = read_go_module(ROOT)
    failures += check("module path", module.module_path, "example.com/proj")
    failures += check("go version", module.go_version, "1.22")
    failures += check("no go.mod", read_go_module("/tmp") .module_path, "")

    matcher = load_gitignore(ROOT)
    failures += check("rule count", len(matcher.rules), 6)
    cases = [
        ("a/b.gen.go", False, True),
        ("b.gen.go", False, True),
        ("keep.gen.go", False, False),
        ("pkg/keep.gen.go", False, False),
        ("tmp/t.go", False, True),
        ("tmp", True, True),
        ("a/tmp/x.go", False, True),
        ("dep/tmp", True, True),
        ("root_only.go", False, True),
        ("pkg/root_only.go", False, False),
        ("deep/d.go", False, True),
        ("x/deep/d.go", False, True),
        ("pkg/sub/ok.go", False, False),
    ]
    for path, is_dir, expected in cases:
        failures += check(f"ignore {path!r} dir={is_dir}", matcher.match(path, is_dir), expected)

    result = discover(ROOT)
    print("\nroot:", result.root)
    print("ignored files:", result.ignored_files)
    for go_file in result.files:
        print(f"  {go_file.rel_path:<24} dir={go_file.directory!r:<12} test={go_file.is_test_file}")
    failures += check(
        "indexed files",
        [go_file.rel_path for go_file in result.files],
        ["keep.gen.go", "main.go", "pkg/a.go", "pkg/a_test.go", "pkg/root_only.go", "pkg/sub/b.go"],
    )
    failures += check("ignored count", result.ignored_files, 4)

    extra = discover(ROOT, extra_ignores=("pkg/**", "!pkg/a.go"))
    failures += check(
        "extra ignores",
        [go_file.rel_path for go_file in extra.files],
        ["keep.gen.go", "main.go", "pkg/a.go"],
    )
    no_gitignore = discover(ROOT, use_gitignore=False)
    failures += check("use_gitignore=False", [g.rel_path for g in no_gitignore.files], [
        "deep/d.go",
        "gen.gen.go",
        "keep.gen.go",
        "main.go",
        "pkg/a.go",
        "pkg/a_test.go",
        "pkg/root_only.go",
        "pkg/sub/b.go",
        "root_only.go",
        "tmp/t.go",
        "x/deep/d.go",
    ])

    paths = [
        package_path_for("example.com/proj", "pkg/sub"),
        package_path_for("example.com/proj", ""),
        package_path_for("example.com/proj", "pkg", "_test"),
        package_path_for("", "pkg/sub"),
        package_path_for("", ""),
    ]
    failures += check(
        "package paths",
        paths,
        ["example.com/proj/pkg/sub", "example.com/proj", "example.com/proj/pkg_test", "./pkg/sub", "."],
    )

    print(f"\n{'ALL CHECKS PASSED' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
