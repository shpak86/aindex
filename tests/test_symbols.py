"""Symbols: ids, kinds, qualified names, member summaries and disambiguation."""

from __future__ import annotations

import unittest

from aindex.constants import (
    KIND_CONST,
    KIND_FUNC,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_STRUCT,
    KIND_TEST,
)
from aindex.goparse import RawDecl, parse_source
from aindex.model import SymbolRecord, truncate_text
from aindex.symbols import (
    body_span,
    disambiguate_ids,
    member_summaries,
    package_symbol,
    qualified_name_of,
    receiver_type_params,
    result_arity,
    result_type_ref,
    symbol_from_decl,
    symbol_id,
    symbol_kind,
    symbols_for_file,
)
from tests.support import (
    MODE_LINUX_GO,
    MODE_WINDOWS_GO,
    STORE_GO,
    STORE_TEST_GO,
)

PACKAGE_PATH = "example.com/demo/internal/store"


def records(text: str, rel_path: str = "internal/store/store.go", package_name: str = "store"):
    parsed = parse_source(text.encode("utf-8"), rel_path)
    return parsed, symbols_for_file(parsed, package_name, PACKAGE_PATH)


class IdTest(unittest.TestCase):
    def test_id_layout(self) -> None:
        self.assertEqual(symbol_id(KIND_STRUCT, PACKAGE_PATH, "Item"), f"struct:{PACKAGE_PATH}:Item")

    def test_method_ids_carry_the_receiver_type(self) -> None:
        _, symbols = records(STORE_GO)
        ids = {symbol.id for symbol in symbols}
        self.assertIn(f"method:{PACKAGE_PATH}:memory.Get", ids)
        self.assertIn(f"method:{PACKAGE_PATH}:memory.lookup", ids)

    def test_every_id_in_a_file_is_unique(self) -> None:
        _, symbols = records(STORE_GO)
        ids = [symbol.id for symbol in symbols]
        self.assertEqual(len(ids), len(set(ids)))


class KindTest(unittest.TestCase):
    def test_kinds_are_reported_precisely(self) -> None:
        _, symbols = records(STORE_GO)
        kinds = {symbol.name: symbol.kind for symbol in symbols}
        self.assertEqual(kinds["Item"], KIND_STRUCT)
        self.assertEqual(kinds["Kind"], "type")
        self.assertEqual(kinds["Store"], KIND_INTERFACE)
        self.assertEqual(kinds["New"], KIND_FUNC)
        self.assertEqual(kinds["Get"], KIND_METHOD)
        self.assertEqual(kinds["KindAnswer"], KIND_CONST)

    def test_test_functions_get_the_test_kind(self) -> None:
        _, symbols = records(STORE_TEST_GO, "internal/store/store_test.go")
        self.assertEqual([symbol.kind for symbol in symbols], [KIND_TEST])
        self.assertTrue(symbols[0].test_related)

    def test_exported_flag_matches_the_name(self) -> None:
        _, symbols = records(STORE_GO)
        exported = {symbol.name: symbol.exported for symbol in symbols}
        self.assertTrue(exported["Item"])
        self.assertFalse(exported["memory"])


class QualifiedNameTest(unittest.TestCase):
    def test_plain_declarations_use_their_name(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        item = next(decl for decl in parsed.decls if decl.name == "Item")
        self.assertEqual(qualified_name_of(item), "Item")
        self.assertEqual(symbol_kind(item), KIND_STRUCT)

    def test_methods_are_qualified_by_their_receiver(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        get = next(decl for decl in parsed.decls if decl.name == "Get")
        self.assertEqual(qualified_name_of(get), "memory.Get")


class MemberSummaryTest(unittest.TestCase):
    def test_struct_fields(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        item = next(decl for decl in parsed.decls if decl.name == "Item")
        self.assertEqual(member_summaries(item), ["ID string", "Kind Kind"])

    def test_struct_fields_keep_tags_and_embedded_markers(self) -> None:
        parsed = parse_source(
            b"""package p

type Base struct{}

type Box struct {
\tBase
\tValue int `json:"value"`
}
""",
            "p/p.go",
        )
        box = next(decl for decl in parsed.decls if decl.name == "Box")
        self.assertEqual(member_summaries(box), ["Base [embedded]", "Value int `json:\"value\"`"])

    def test_interface_method_specs(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        store = next(decl for decl in parsed.decls if decl.name == "Store")
        self.assertEqual(
            member_summaries(store), ["Get(ctx context.Context, id string) (Item, error)"]
        )

    def test_functions_have_no_members(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        new = next(decl for decl in parsed.decls if decl.name == "New")
        self.assertEqual(member_summaries(new), [])


class ReceiverTypeParamsTest(unittest.TestCase):
    def test_generic_receiver(self) -> None:
        self.assertEqual(receiver_type_params("*List[T]"), frozenset({"T"}))
        self.assertEqual(receiver_type_params("*Pair[K, V]"), frozenset({"K", "V"}))

    def test_plain_receiver(self) -> None:
        self.assertEqual(receiver_type_params("*memory"), frozenset())
        self.assertEqual(receiver_type_params(""), frozenset())


class BodySpanTest(unittest.TestCase):
    def test_function_body_is_the_span(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        new = next(decl for decl in parsed.decls if decl.name == "New")
        span = body_span(new)
        self.assertIsNotNone(span)
        start, end = span
        self.assertLess(start, end)
        self.assertIn("{", parsed.lexed.src[start:end])

    def test_interface_body_is_skipped(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        store = next(decl for decl in parsed.decls if decl.name == "Store")
        self.assertIsNone(body_span(store))

    def test_const_and_var_specs_use_the_whole_spec(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        missing = next(decl for decl in parsed.decls if decl.name == "ErrMissing")
        self.assertEqual(body_span(missing), (missing.start_byte, missing.end_byte))

    def test_declarations_without_a_body_have_no_span(self) -> None:
        parsed = parse_source(b"package p\n\nfunc Assembly()\n", "p/asm.go")
        self.assertIsNone(body_span(parsed.decls[0]))


class ResultTypeRefTest(unittest.TestCase):
    """Which declarations name exactly one project type, and how many values they return."""

    def decl(self, source: str) -> RawDecl:
        parsed = parse_source(source.encode("utf-8"), "p/p.go")
        self.assertEqual(len(parsed.decls), 1)
        return parsed.decls[0]

    def test_plain_and_pointer_results_name_one_type(self) -> None:
        for source, chain in (
            ("func f() *ProxyServer { return nil }", "ProxyServer"),
            ("func f() *memory { return nil }", "memory"),
            ("func f() memory { return memory{} }", "memory"),
            ("func f() http.Handler { return nil }", "http.Handler"),
        ):
            decl = self.decl(source)
            ref = result_type_ref(decl)
            self.assertIsNotNone(ref, source)
            self.assertEqual(ref.chain, chain)
            self.assertEqual(result_arity(decl), 1)

    def test_multi_result_and_composite_shapes_give_no_type(self) -> None:
        for source, arity in (
            ("func f() error { return nil }", 1),
            ("func f() string { return \"\" }", 1),
            ("func f() []T { return nil }", 1),
            ("func f() map[string]Item { return nil }", 1),
            ("func f() chan int { return nil }", 1),
            ("func f() func() error { return nil }", 1),
            ("func f() (v *Item) { return }", 1),
            ("func f() (Item, error) { return Item{}, nil }", 2),
            ("func f() (a, b int) { return 0, 0 }", 2),
            ("func f() { }", 0),
        ):
            decl = self.decl(source)
            self.assertIsNone(result_type_ref(decl), source)
            self.assertEqual(result_arity(decl), arity, source)

    def test_real_declarations_from_the_demo(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        by_name = {decl.name: decl for decl in parsed.decls}
        new_ref = result_type_ref(by_name["New"])
        self.assertIsNotNone(new_ref)
        self.assertEqual(new_ref.chain, "memory")
        self.assertEqual(result_arity(by_name["New"]), 1)
        self.assertIsNone(result_type_ref(by_name["Get"]))
        self.assertEqual(result_arity(by_name["Get"]), 2)
        self.assertEqual(result_arity(by_name["Put"]), 0)


class DisambiguateIdsTest(unittest.TestCase):
    def test_colliding_ids_get_a_file_suffix(self) -> None:
        linux = parse_source(MODE_LINUX_GO.encode("utf-8"), "platform/mode_linux.go")
        windows = parse_source(MODE_WINDOWS_GO.encode("utf-8"), "platform/mode_windows.go")
        symbols = [
            symbol_from_decl(linux.decls[0], linux, "platform", "example.com/demo/platform"),
            symbol_from_decl(windows.decls[0], windows, "platform", "example.com/demo/platform"),
        ]
        self.assertEqual(symbols[0].id, symbols[1].id)
        self.assertEqual(disambiguate_ids(symbols), 2)
        self.assertEqual(
            sorted(symbol.id for symbol in symbols),
            [
                "const:example.com/demo/platform:Mode#platform/mode_linux.go",
                "const:example.com/demo/platform:Mode#platform/mode_windows.go",
            ],
        )

    def test_unique_ids_are_left_alone(self) -> None:
        _, symbols = records(STORE_GO)
        before = [symbol.id for symbol in symbols]
        self.assertEqual(disambiguate_ids(symbols), 0)
        self.assertEqual([symbol.id for symbol in symbols], before)


class SymbolFromDeclTest(unittest.TestCase):
    def test_record_carries_file_facts(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        item = next(decl for decl in parsed.decls if decl.name == "Item")
        record = symbol_from_decl(item, parsed, "store", PACKAGE_PATH)
        self.assertEqual(record.file, "internal/store/store.go")
        self.assertEqual(record.package, "store")
        self.assertEqual(record.package_path, PACKAGE_PATH)
        self.assertEqual(record.content_hash, parsed.content_hash)
        self.assertEqual(record.imports, ["context", "fmt", "sync"])
        self.assertEqual(record.fields, ["ID string", "Kind Kind"])
        self.assertEqual(record.receiver_type, "")
        self.assertEqual((record.start_line, record.end_line), (11, 14))

    def test_method_records_keep_the_receiver_type(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        get = next(decl for decl in parsed.decls if decl.name == "Get")
        record = symbol_from_decl(get, parsed, "store", PACKAGE_PATH)
        self.assertEqual(record.receiver, "*memory")
        self.assertEqual(record.receiver_type, "memory")
        self.assertEqual(record.qualified_name, "memory.Get")

    def test_doc_is_truncated_to_the_limit(self) -> None:
        text = "package p\n\n// " + "x" * 80 + "\nfunc Long() {}\n"
        parsed = parse_source(text.encode("utf-8"), "p/p.go")
        record = symbol_from_decl(parsed.decls[0], parsed, "p", "example.com/p", 20)
        self.assertEqual(len(record.doc), 20)
        self.assertTrue(record.doc.endswith("..."))

    def test_receiver_type_is_part_of_the_serialised_record(self) -> None:
        parsed = parse_source(STORE_GO.encode("utf-8"), "internal/store/store.go")
        get = next(decl for decl in parsed.decls if decl.name == "Get")
        payload = symbol_from_decl(get, parsed, "store", PACKAGE_PATH).to_dict()
        self.assertEqual(payload["receiver_type"], "memory")
        self.assertEqual(payload["kind"], KIND_METHOD)


class PackageSymbolTest(unittest.TestCase):
    def package(self, symbols: list[SymbolRecord]) -> object:
        from aindex.model import PackageInfo

        return PackageInfo(
            path=PACKAGE_PATH,
            name="store",
            directory="internal/store",
            doc="Package store keeps items in memory.",
            files=["internal/store/store.go"],
            symbols=symbols,
        )

    def test_package_record_describes_the_package(self) -> None:
        _, symbols = records(STORE_GO)
        record = package_symbol(self.package(symbols))
        self.assertEqual(record.id, f"package:{PACKAGE_PATH}:store")
        self.assertEqual(record.kind, KIND_PACKAGE)
        self.assertEqual(record.signature, "package store")
        self.assertEqual(record.doc, "Package store keeps items in memory.")
        self.assertEqual(record.imports, ["context", "fmt", "sync"])
        self.assertEqual(record.file, "")
        self.assertTrue(record.exported)
        self.assertEqual(record.start_line, 0)

    def test_package_doc_can_be_overridden(self) -> None:
        record = package_symbol(self.package([]), doc="Given doc.")
        self.assertEqual(record.doc, "Given doc.")
        self.assertEqual(record.imports, [])


class TruncateTextTest(unittest.TestCase):
    def test_short_text_is_untouched(self) -> None:
        self.assertEqual(truncate_text("short", 100), "short")

    def test_long_text_keeps_the_suffix_inside_the_limit(self) -> None:
        self.assertEqual(truncate_text("x" * 50, 10), "xxxxxxx...")

    def test_limit_zero_disables_truncation(self) -> None:
        self.assertEqual(truncate_text("x" * 50, 0), "x" * 50)


if __name__ == "__main__":
    unittest.main()
