"""Call/type reference extraction and conservative name resolution.

The unit tests below pin the *rules* (what counts as a call site, what a name
resolves to, what stays unresolved); the integration tests pin them against the
demo project so that a change in either half of the pair is caught.
"""

from __future__ import annotations

import unittest

from aindex.calls import (
    NameRef,
    ResolveContext,
    SymbolIndex,
    SymbolLinks,
    collect_call_refs,
    collect_signature_refs,
    collect_type_refs,
    dedupe_refs,
    link_symbols,
    resolve_ref,
)
from aindex.constants import (
    KIND_FUNC,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_STRUCT,
)
from aindex.golex import lex
from aindex.goparse import parse_source
from aindex.model import SymbolRecord
from aindex.symbols import body_span, resolve_context, signature_refs, symbols_for_file
from tests.support import IndexTestCase

STORE = "example.com/demo/internal/store"
MAIN = "example.com/demo"
API = "example.com/demo/internal/api"


def chains(refs: list[NameRef]) -> list[str]:
    return [ref.chain for ref in refs]


def collect(text: str) -> tuple[list[NameRef], list[NameRef]]:
    """Return the call and composite-literal refs of a whole snippet."""
    lexed = lex(text.encode("utf-8"))
    return (
        collect_call_refs(lexed, 0, len(lexed.src)),
        collect_type_refs(lexed, 0, len(lexed.src)),
    )


def record(symbol_id: str, kind: str, name: str, **overrides: object) -> SymbolRecord:
    """A minimal-but-valid :class:`SymbolRecord` for index tests."""
    fields: dict[str, object] = {
        "package": "store",
        "package_path": STORE,
        "file": "store.go",
        "qualified_name": name,
        "receiver": "",
        "signature": f"{kind} {name}",
        "doc": "",
        "exported": name[:1].isupper(),
        "start_line": 1,
        "end_line": 1,
        "start_byte": 0,
        "end_byte": 0,
    }
    fields.update(overrides)
    return SymbolRecord(id=symbol_id, kind=kind, name=name, **fields)  # type: ignore[arg-type]


class CollectCallRefsTest(unittest.TestCase):
    def test_plain_call_keeps_the_chain(self) -> None:
        calls, _ = collect("helper(1); x.Method(); pkg.New()")
        self.assertEqual(chains(calls), ["helper", "x.Method", "pkg.New"])

    def test_chain_base_and_name_are_split(self) -> None:
        calls, _ = collect("pkg.New()")
        self.assertEqual((calls[0].base, calls[0].name), ("pkg", "New"))

    def test_line_numbers_are_recorded(self) -> None:
        calls, _ = collect("\tfirst()\n\tsecond()\n")
        self.assertEqual([ref.line for ref in calls], [1, 2])

    def test_predeclared_builtins_are_not_calls(self) -> None:
        calls, _ = collect("make([]int, 0); len(s); append(s, 1); new(T)")
        self.assertEqual(calls, [])

    def test_builtin_type_conversions_are_skipped(self) -> None:
        calls, _ = collect("string(b); int64(n); error(err)")
        self.assertEqual(calls, [])

    def test_function_declarations_inside_the_scanned_range_are_refs(self) -> None:
        # Which is exactly why the pipeline scans the *body* range, not the file.
        calls, _ = collect("func f() { helper() }")
        self.assertEqual(chains(calls), ["f", "helper"])

    def test_only_the_requested_byte_range_is_scanned(self) -> None:
        text = "func a() { early() }\nfunc b() { late() }\n"
        lexed = lex(text.encode("utf-8"))
        refs = collect_call_refs(lexed, text.index("lat"), len(lexed.src))
        self.assertEqual(chains(refs), ["late"])


class CollectTypeRefsTest(unittest.TestCase):
    def test_composite_literal_is_a_type_reference(self) -> None:
        _, types = collect("func f() { _ = Item{ID: 1} }")
        self.assertEqual(chains(types), ["Item"])

    def test_qualified_composite_literal(self) -> None:
        _, types = collect("func f() { _ = store.Item{} }")
        self.assertEqual(chains(types), ["store.Item"])

    def test_block_conditions_are_not_literals(self) -> None:
        _, types = collect("func f() { if ok {\n}\n for i := range items {\n} }")
        self.assertEqual(chains(types), [])

    def test_nested_literal_types_are_all_reported(self) -> None:
        _, types = collect("func f() { _ = Outer{Inner: Inner{}} }")
        self.assertEqual(chains(types), ["Outer", "Inner"])

    def test_map_and_slice_literals_report_the_element_type(self) -> None:
        _, types = collect('func f() { _ = map[string]Item{"a": Item{}} }')
        self.assertEqual(chains(types), ["Item"])


class CollectSignatureRefsTest(unittest.TestCase):
    def test_qualified_type_in_a_parameter_list(self) -> None:
        refs = collect_signature_refs("ctx context.Context")
        self.assertIn("context.Context", chains(refs))
        self.assertIn("ctx", chains(refs))

    def test_pointer_and_slice_types(self) -> None:
        self.assertEqual(chains(collect_signature_refs("[]*Service")), ["Service"])

    def test_generic_instantiation_keeps_both_names(self) -> None:
        self.assertEqual(chains(collect_signature_refs("List[Item]")), ["List", "Item"])

    def test_empty_fragment_yields_nothing(self) -> None:
        self.assertEqual(collect_signature_refs("   "), [])


class DedupeRefsTest(unittest.TestCase):
    def test_first_occurrence_wins(self) -> None:
        refs = [
            NameRef("New", "New", "New", 1),
            NameRef("pkg.New", "pkg", "New", 2),
            NameRef("New", "New", "New", 3),
        ]
        unique = dedupe_refs(refs)
        self.assertEqual(chains(unique), ["New", "pkg.New"])
        self.assertEqual(unique[0].line, 1)


class SymbolIndexTest(unittest.TestCase):
    def build(self) -> SymbolIndex:
        index = SymbolIndex()
        index.add(record(f"package:{STORE}:store", KIND_PACKAGE, ""))
        index.add(record(f"func:{STORE}:New", KIND_FUNC, "New"))
        index.add(record(f"func:{STORE}:newInternal", KIND_FUNC, "newInternal"))
        index.add(
            record(
                f"method:{STORE}:memory.Get",
                KIND_METHOD,
                "Get",
                receiver="*memory",
                receiver_type="memory",
            )
        )
        return index

    def test_package_rows_are_not_indexed(self) -> None:
        index = SymbolIndex()
        index.add(record(f"package:{STORE}:store", KIND_PACKAGE, ""))
        self.assertEqual(index.scope, {})
        self.assertEqual(index.kinds, {})

    def test_local_names_resolve_regardless_of_case(self) -> None:
        index = self.build()
        self.assertEqual(index.local(STORE, "New"), f"func:{STORE}:New")
        self.assertEqual(index.local(STORE, "newInternal"), f"func:{STORE}:newInternal")
        self.assertEqual(index.local(STORE, "missing"), "")
        self.assertEqual(index.local("example.com/other", "New"), "")

    def test_only_exported_names_are_importable(self) -> None:
        index = self.build()
        self.assertEqual(index.imported(STORE, "New"), f"func:{STORE}:New")
        self.assertEqual(index.imported(STORE, "newInternal"), "")

    def test_methods_are_keyed_by_the_base_receiver_type(self) -> None:
        index = self.build()
        self.assertEqual(index.method(STORE, "memory", "Get"), f"method:{STORE}:memory.Get")
        self.assertEqual(index.method(STORE, "*memory", "Get"), f"method:{STORE}:memory.Get")
        self.assertEqual(index.method(STORE, "memory", "Put"), "")
        self.assertEqual(index.methods_of(STORE, "*memory"), {"Get": f"method:{STORE}:memory.Get"})

    def test_kinds_are_recorded(self) -> None:
        index = self.build()
        self.assertEqual(index.kind(f"func:{STORE}:New"), KIND_FUNC)
        self.assertEqual(index.kind("nope"), "")

    def test_first_declaration_wins_for_duplicate_names(self) -> None:
        index = SymbolIndex()
        index.add(record(f"func:{STORE}:New", KIND_FUNC, "New"))
        index.add(record(f"var:{STORE}:New", "var", "New"))
        self.assertEqual(index.local(STORE, "New"), f"func:{STORE}:New")


class ResolveRefTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index = SymbolIndex()
        self.index.add(record(f"func:{STORE}:New", KIND_FUNC, "New"))
        self.index.add(record(f"struct:{STORE}:memory", KIND_STRUCT, "memory"))
        self.index.add(
            record(
                f"method:{STORE}:memory.Get",
                KIND_METHOD,
                "Get",
                receiver="*memory",
                receiver_type="memory",
            )
        )
        self.index.add(
            record(f"struct:{API}:Handler", KIND_STRUCT, "Handler", package_path=API)
        )
        self.ctx = ResolveContext(package_path=STORE, imports={"api": API, "store": STORE})

    def ref(self, chain: str, ctx: ResolveContext | None = None) -> str:
        parts = chain.split(".")
        return resolve_ref(
            NameRef(chain, parts[0], parts[-1], 1), self.index, ctx or self.ctx
        )

    def test_package_local_name(self) -> None:
        self.assertEqual(self.ref("New"), f"func:{STORE}:New")

    def test_unknown_local_name_stays_unresolved(self) -> None:
        self.assertEqual(self.ref("nope"), "")

    def test_import_of_the_own_package_resolves_locally(self) -> None:
        self.assertEqual(self.ref("store.New"), f"func:{STORE}:New")

    def test_symbol_of_an_imported_package(self) -> None:
        self.assertEqual(self.ref("api.Handler"), f"struct:{API}:Handler")

    def test_unexported_symbol_of_another_package_stays_unresolved(self) -> None:
        self.assertEqual(self.ref("api.handler"), "")

    def test_chain_deeper_than_package_dot_name_stays_unresolved(self) -> None:
        self.assertEqual(self.ref("api.sub.Handler"), "")

    def test_import_outside_the_project_stays_unresolved(self) -> None:
        self.assertEqual(self.ref("context.Background"), "")

    def test_method_on_the_enclosing_receiver(self) -> None:
        ctx = ResolveContext(
            package_path=STORE,
            imports={"store": STORE},
            receiver_var="m",
            receiver_type="memory",
        )
        self.assertEqual(self.ref("m.Get", ctx), f"method:{STORE}:memory.Get")

    def test_chain_through_a_receiver_field_stays_unresolved(self) -> None:
        ctx = ResolveContext(
            package_path=STORE, receiver_var="m", receiver_type="memory"
        )
        self.assertEqual(self.ref("m.mu.Lock", ctx), "")

    def test_method_expression_on_a_local_type(self) -> None:
        self.assertEqual(self.ref("memory.Get"), f"method:{STORE}:memory.Get")

    def test_type_parameter_names_never_resolve(self) -> None:
        ctx = ResolveContext(
            package_path=STORE, imports={"api": API}, type_params=frozenset({"T"})
        )
        self.assertEqual(self.ref("T", ctx), "")
        self.assertEqual(self.ref("T.Get", ctx), "")

    def test_imported_package_shadowed_by_a_type_parameter(self) -> None:
        ctx = ResolveContext(package_path=STORE, imports={"api": API}, type_params=frozenset({"api"}))
        self.assertEqual(self.ref("api.Handler", ctx), "")


def links_for(
    text: str,
    package_path: str = "example.com/p",
    package_name: str = "p",
    rel_path: str = "p/p.go",
) -> tuple[list[SymbolLinks], SymbolIndex, list[SymbolRecord]]:
    """Parse ``text`` and build the linking inputs the pipeline would build."""
    parsed = parse_source(text.encode("utf-8"), rel_path)
    records = symbols_for_file(parsed, package_name, package_path)
    index = SymbolIndex()
    for record_ in records:
        index.add(record_)
    links = [
        SymbolLinks(
            record_,
            resolve_context(decl, parsed, package_path),
            parsed.lexed,
            body_span(decl),
            signature_refs(decl),
        )
        for record_, decl in zip(records, parsed.decls)
    ]
    return links, index, records


class LinkSymbolsTest(unittest.TestCase):
    SOURCE = """package p

func caller() {
\tcallee()
\tpkg.Lost()
}

func callee() {}

// Thing is documented.
type Thing struct{ Name string }

func (t Thing) Get() string { return t.Name }

func makeThing() Thing { return Thing{} }
"""

    def test_stats_summarise_the_linking_run(self) -> None:
        links, index, records = links_for(self.SOURCE)
        stats = link_symbols(links, index)
        self.assertEqual(stats.symbols, len(records))
        self.assertEqual(stats.symbols, 5)
        self.assertEqual(stats.calls, 1)
        self.assertEqual(stats.resolved_calls, 1)
        self.assertEqual(stats.unresolved_calls, 1)
        self.assertEqual(stats.type_refs, sum(len(record_.types) for record_ in records))

    def test_package_local_call_and_reverse_edge(self) -> None:
        links, index, records = links_for(self.SOURCE)
        link_symbols(links, index)
        by_name = {record_.name: record_ for record_ in records}
        self.assertEqual(by_name["caller"].calls, ["func:example.com/p:callee"])
        self.assertEqual(by_name["caller"].unresolved_calls, ["pkg.Lost"])
        self.assertEqual(by_name["callee"].callers, ["func:example.com/p:caller"])

    def test_composite_literal_becomes_a_type_edge_not_a_call(self) -> None:
        links, index, records = links_for(self.SOURCE)
        link_symbols(links, index)
        make = next(record_ for record_ in records if record_.name == "makeThing")
        self.assertEqual(make.calls, [])
        self.assertEqual(make.types, ["struct:example.com/p:Thing"])

    def test_type_symbol_lists_its_methods(self) -> None:
        links, index, records = links_for(self.SOURCE)
        link_symbols(links, index)
        thing = next(record_ for record_ in records if record_.name == "Thing")
        self.assertEqual(thing.methods, ["method:example.com/p:Thing.Get"])

    def test_receiver_method_body_is_linked(self) -> None:
        links, index, records = links_for(self.SOURCE)
        link_symbols(links, index)
        get = next(record_ for record_ in records if record_.name == "Get")
        self.assertEqual(get.types, ["struct:example.com/p:Thing"])
        self.assertEqual(get.unresolved_calls, [])


class DemoCallGraphTest(IndexTestCase):
    """The rules again, this time through the whole pipeline on the demo project."""

    def setUp(self) -> None:
        super().setUp()
        self.result = self.build()

    def test_package_local_call(self) -> None:
        main = self.symbol(f"func:{MAIN}:main")
        self.assertEqual(main.calls, [f"func:{MAIN}:run"])
        self.assertEqual(main.unresolved_calls, ["context.Background", "fmt.Println"])

    def test_imported_call_and_cross_package_callers(self) -> None:
        run = self.symbol(f"func:{MAIN}:run")
        new = self.symbol(f"func:{STORE}:New")
        self.assertEqual(run.calls, [f"func:{STORE}:New"])
        self.assertEqual(run.types, [f"struct:{STORE}:Item"])
        self.assertEqual(run.unresolved_calls, ["items.Get"])
        self.assertEqual(
            new.callers,
            [f"func:{MAIN}:run", f"test:{STORE}:TestMemoryGet"],
        )

    def test_receiver_method_calls_and_reverse_edges(self) -> None:
        get = self.symbol(f"method:{STORE}:memory.Get")
        lookup = self.symbol(f"method:{STORE}:memory.lookup")
        self.assertEqual(get.calls, [f"method:{STORE}:memory.lookup"])
        self.assertEqual(lookup.callers, [f"method:{STORE}:memory.Get"])
        self.assertEqual(lookup.unresolved_calls, ["m.mu.Lock", "m.mu.Unlock"])

    def test_method_chained_through_a_field_is_unresolved(self) -> None:
        get = self.symbol(f"method:{API}:Handler.Get")
        self.assertEqual(get.unresolved_calls, ["h.store.Get"])
        self.assertEqual(get.types, [f"struct:{API}:Handler", f"struct:{STORE}:Item"])

    def test_type_symbol_lists_its_methods(self) -> None:
        memory = self.symbol(f"struct:{STORE}:memory")
        self.assertEqual(
            memory.methods,
            [
                f"method:{STORE}:memory.Get",
                f"method:{STORE}:memory.Put",
                f"method:{STORE}:memory.lookup",
                f"method:{STORE}:memory.save",
            ],
        )

    def test_generic_type_parameter_calls_stay_unresolved(self) -> None:
        map_func = self.symbol(f"func:{API}:Map")
        self.assertEqual(map_func.unresolved_calls, ["f"])
        self.assertEqual(map_func.calls, [])

    def test_external_test_package_calls_the_package_under_test(self) -> None:
        test = self.symbol(f"test:{API}_test:TestHandlerGet")
        self.assertEqual(test.calls, [f"func:{API}:NewHandler"])
        self.assertIn("t.Fatalf", test.unresolved_calls)

    def test_stats_match_the_symbol_records(self) -> None:
        # ``stats.calls`` counts every call site; the records only hold the ones
        # that resolved (``calls``) plus the raw spellings that did not.
        stats = self.result.stats
        declared = [record for record in self.result.symbols if not record.is_package]
        self.assertEqual(stats.symbols, len(declared))
        self.assertEqual(stats.resolved_calls, sum(len(record.calls) for record in declared))
        self.assertEqual(
            stats.unresolved_calls,
            sum(len(record.unresolved_calls) for record in declared),
        )
        self.assertEqual(stats.calls, stats.resolved_calls + stats.unresolved_calls)
        self.assertLess(stats.resolved_calls, stats.unresolved_calls)


if __name__ == "__main__":
    unittest.main()
