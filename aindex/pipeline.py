"""One indexing run: discover, parse, link, then write the index.

The pipeline is a pure function of the project tree plus :class:`IndexOptions`:

``build_index``
    read the tree, parse every Go file, group declarations into packages, build
    the symbol records, resolve references and produce an :class:`IndexResult`
    (nothing is written to disk);
``write_index``
    serialize that result into the ``.ai-index`` directory;
``run``
    the two together, which is what the CLI calls.

Nothing here shells out and nothing is written outside the output directory.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .calls import SymbolIndex, SymbolLinks, link_symbols
from .constants import (
    DEFAULT_MAX_DOC_CHARS,
    DEFAULT_OUTPUT_DIR,
    TEST_PACKAGE_SUFFIX,
)
from .discovery import discover, package_path_for
from .goparse import ParsedGoFile, parse_source
from .model import (
    FileRecord,
    IndexResult,
    PackageInfo,
    Stats,
    SymbolRecord,
    package_order_key,
)
from .render import package_pages, write_markdown
from .serialize import ensure_directory, write_files, write_stats, write_symbols
from .symbols import (
    body_span,
    disambiguate_ids,
    package_symbol,
    resolve_context,
    result_arity,
    result_type_ref,
    signature_refs,
    symbols_for_file,
)

#: Backend reported in ``stats.json``; the internal parser needs no dependency.
BACKEND_PYTHON = "python"

#: Files without a package clause cannot be attributed to a package.
MISSING_PACKAGE_ERROR = "missing package clause"


@dataclass(slots=True)
class IndexOptions:
    """Everything the CLI can configure for one run."""

    root: str = "."
    output_dir: str = DEFAULT_OUTPUT_DIR
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS
    extra_ignores: tuple[str, ...] = ()
    use_gitignore: bool = True
    include_tests: bool = True
    clean: bool = False

    def resolved_root(self) -> str:
        return os.path.abspath(self.root)

    def resolved_output_dir(self) -> str:
        """Output directory; a relative value is relative to the project root."""
        path = self.output_dir or DEFAULT_OUTPUT_DIR
        if not os.path.isabs(path):
            path = os.path.join(self.resolved_root(), path)
        return os.path.normpath(path)



def timestamp() -> str:
    """UTC timestamp in ISO 8601; honours ``SOURCE_DATE_EPOCH`` for reproducibility."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _directory_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def _output_ignore(options: IndexOptions) -> str:
    """Anchored ignore pattern that keeps the output directory out of the index."""
    root = options.resolved_root()
    index_dir = options.resolved_output_dir()
    if index_dir == root or not index_dir.startswith(root + os.sep):
        return ""
    rel = os.path.relpath(index_dir, root).replace(os.sep, "/")
    return f"/{rel}/"


def _parse_file(rel_path: str, path: str) -> tuple[ParsedGoFile | None, str]:
    """Parse one file; returns the parsed file (or ``None``) and an error message."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        return parse_source(raw, rel_path), ""
    except OSError as error:
        return None, f"cannot read file: {error}"
    except Exception as error:  # a parser bug must not abort the whole run
        return None, f"{type(error).__name__}: {error}"



def build_index(options: IndexOptions) -> IndexResult:
    """Walk, parse and link the project; writes nothing to disk."""
    started = time.perf_counter()
    root = options.resolved_root()
    ignores = list(options.extra_ignores)
    output_pattern = _output_ignore(options)
    if output_pattern:
        ignores.append(output_pattern)
    discovered = discover(
        root, extra_ignores=tuple(ignores), use_gitignore=options.use_gitignore
    )
    module = discovered.module

    parsed: dict[str, ParsedGoFile] = {}
    file_records: list[FileRecord] = []
    parse_errors = 0
    for go_file in discovered.files:
        if not options.include_tests and go_file.is_test_file:
            continue
        parsed_file, error = _parse_file(go_file.rel_path, go_file.path)
        errors: list[str] = []
        if parsed_file is not None:
            errors.extend(parsed_file.errors)
            if not parsed_file.package_name:
                errors.append(MISSING_PACKAGE_ERROR)
        elif error:
            errors.append(error)
        if errors:
            parse_errors += 1
        if parsed_file is not None and parsed_file.package_name:
            # A file with broken bits still carries useful declarations.
            parsed[go_file.rel_path] = parsed_file
        file_records.append(
            FileRecord(
                file=go_file.rel_path,
                package=parsed_file.package_name if parsed_file is not None else "",
                package_path=package_path_for(module.module_path, go_file.directory),
                lines=parsed_file.lines if parsed_file is not None else 0,
                symbols=0,  # filled in below, once the symbols exist
                imports=parsed_file.import_paths if parsed_file is not None else [],
                build_tags=parsed_file.build_tags if parsed_file is not None else [],
                test_related=go_file.is_test_file,
                parse_error=bool(errors),
                error="; ".join(errors),
                content_hash=parsed_file.content_hash if parsed_file is not None else "",
            )
        )

    groups: dict[tuple[str, str], list[ParsedGoFile]] = {}
    for rel_path in sorted(parsed):
        parsed_file = parsed[rel_path]
        groups.setdefault((_directory_of(rel_path), parsed_file.package_name), []).append(
            parsed_file
        )

    packages: dict[str, PackageInfo] = {}
    links: list[SymbolLinks] = []
    symbols_per_file: dict[str, int] = {}
    for key in sorted(groups):
        directory, name = key
        files = sorted(groups[key], key=lambda item: item.rel_path)
        is_test_package = name.endswith(TEST_PACKAGE_SUFFIX)
        package = PackageInfo(
            path=package_path_for(
                module.module_path, directory, TEST_PACKAGE_SUFFIX if is_test_package else ""
            ),
            name=name,
            directory=directory,
            doc=next((item.package_doc for item in files if item.package_doc), ""),
            files=[item.rel_path for item in files],
            test_related=is_test_package,
        )
        for parsed_file in files:
            rows = symbols_for_file(
                parsed_file, name, package.path, options.max_doc_chars
            )
            for decl, symbol in zip(parsed_file.decls, rows):
                links.append(
                    SymbolLinks(
                        symbol=symbol,
                        context=resolve_context(decl, parsed_file, package.path),
                        lexed=parsed_file.lexed,
                        body=body_span(decl),
                        signature_types=signature_refs(decl),
                        param_types=tuple((param.name, param.type) for param in decl.params),
                        field_types=tuple((field.name, field.type) for field in decl.fields),
                        result_ref=result_type_ref(decl),
                        result_arity=result_arity(decl),
                    )
                )
                symbols_per_file[parsed_file.rel_path] = (
                    symbols_per_file.get(parsed_file.rel_path, 0) + 1
                )
            package.symbols.extend(rows)
        packages[package.key] = package

    for record in file_records:
        record.symbols = symbols_per_file.get(record.file, 0)


    # Build-constrained files (``foo_linux.go``/``foo_windows.go``) may declare the
    # same symbol; their ids get a ``#file`` suffix so that ids stay unique.
    disambiguate_ids([link.symbol for link in links])
    index = SymbolIndex()
    for link in links:
        index.add(link.symbol)
    link_stats = link_symbols(links, index)

    records: list[SymbolRecord] = []
    for package in sorted(packages.values(), key=package_order_key):
        records.append(package_symbol(package, max_doc_chars=options.max_doc_chars))
        records.extend(package.symbols)

    stats = Stats(
        generated_at=timestamp(),
        root=root,
        module_path=module.module_path,
        go_version=module.go_version,
        packages=len(packages),
        files=len(file_records),
        symbols=sum(len(package.symbols) for package in packages.values()),
        calls=link_stats.resolved_calls + link_stats.unresolved_calls,
        resolved_calls=link_stats.resolved_calls,
        unresolved_calls=link_stats.unresolved_calls,
        parse_errors=parse_errors,
        duration_seconds=time.perf_counter() - started,
        backend=BACKEND_PYTHON,
        ignored_files=discovered.ignored_files,
    )
    return IndexResult(
        root=root,
        module=module,
        packages=packages,
        symbols=records,
        files=file_records,
        stats=stats,
    )


def write_index(result: IndexResult, index_dir: str) -> list[str]:
    """Serialize ``result`` into ``index_dir``; returns every path written."""
    ensure_directory(index_dir)
    pages = package_pages(result.packages.values())
    written = [
        write_symbols(index_dir, result.symbols),
        write_files(index_dir, result.files),
        write_stats(index_dir, result.stats),
    ]
    written.extend(write_markdown(index_dir, result, pages))
    return written


def run(options: IndexOptions) -> tuple[IndexResult, list[str]]:
    """Build the index and write it to disk.

    ``options.clean`` removes the output directory first, which is refused when
    that directory is the project root or one of its parents.
    """
    index_dir = options.resolved_output_dir()
    root = options.resolved_root()
    if index_dir == root or root.startswith(index_dir + os.sep):
        raise ValueError(f"refusing to use {index_dir!r} as output directory")
    if options.clean and os.path.isdir(index_dir):
        shutil.rmtree(index_dir)
    result = build_index(options)
    return result, write_index(result, index_dir)

