"""Multi-language repository intelligence tests — scanning, tree-sitter
parsing (python/js/ts), the regex fallback parser (go), and the
symbol-level call/reference graph with cross-file resolution. Real
files in tmp dirs; no mocks."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import (  # noqa: E402
    RepositoryIndex,
    RepositoryScanner,
    language_of,
    path_to_module,
)
from mini_claude.repo.symbols import SymbolKind  # noqa: E402


def _mixed_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "utils").mkdir(parents=True)
    (repo / "app").mkdir(parents=True)
    (repo / "svc").mkdir(parents=True)
    (repo / "pkg" / "core.py").write_text(
        "def helper(x):\n    return x + 1\n\n\n"
        "def consumer():\n    return helper(1)\n")
    (repo / "pkg" / "service.py").write_text(
        "from pkg.core import helper\n\n\n"
        "def run():\n    return helper(2)\n")
    (repo / "utils" / "math.js").write_text(
        "export function add(a, b) { return a + b; }\n"
        "export function multiply(a, b) { return a * b; }\n")
    (repo / "utils" / "main.js").write_text(
        "const { add, multiply } = require('./math');\n"
        "function compute() { return add(1, 2) + multiply(3, 4); }\n"
        "module.exports = { compute };\n")
    (repo / "app" / "types.ts").write_text(
        "export interface Shape {\n  area(): number;\n}\n"
        "export class Circle implements Shape {\n"
        "  constructor(public r: number) {}\n"
        "  area() { return 3.14 * this.r * this.r; }\n"
        "}\n"
        "export const describe = (c: Circle): string => `circle ${c.area()}`;\n")
    (repo / "svc" / "handler.go").write_text(
        'package svc\n\nimport "fmt"\n\ntype Handler struct{}\n\n'
        'func (h *Handler) Handle() {\n\tfmt.Println(helper())\n}\n\n'
        'func helper() string {\n\treturn "ok"\n}\n')
    return repo


class TestLanguageDetection(unittest.TestCase):
    def test_extension_map(self):
        self.assertEqual(language_of("a.py"), "python")
        self.assertEqual(language_of("a.js"), "javascript")
        self.assertEqual(language_of("a.mjs"), "javascript")
        self.assertEqual(language_of("a.ts"), "typescript")
        self.assertEqual(language_of("a.tsx"), "tsx")
        self.assertEqual(language_of("a.go"), "go")
        self.assertEqual(language_of("a.java"), "java")
        self.assertIsNone(language_of("README.md"))

    def test_module_naming(self):
        self.assertEqual(path_to_module("pkg/sub/helper.py"), "pkg.sub.helper")
        self.assertEqual(path_to_module("pkg/__init__.py"), "pkg")
        self.assertEqual(path_to_module("src/utils/index.js"), "src.utils")
        self.assertEqual(path_to_module("src/utils/index.ts"), "src.utils")
        self.assertEqual(path_to_module("src/utils/helper.ts"), "src.utils.helper")
        self.assertIsNone(path_to_module("weird-name.py"))


class TestMultiLanguageIndex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _mixed_repo(Path(self._tmp.name))
        self.index = RepositoryIndex(self.repo)

    def test_scanner_collects_every_language(self):
        result = self.index.build()
        self.assertEqual(result.files, 6)
        self.assertEqual(result.files_by_language,
                         {"javascript": 2, "python": 2, "go": 1, "typescript": 1})
        self.assertEqual(result.parser_kinds["go"], "FallbackParser")
        self.assertEqual(result.parser_kinds["typescript"], "JsParser")

    def test_js_symbols_and_bindings(self):
        self.index.build()
        names = {(s.name, s.kind) for s in self.index.symbols_in_file("utils/main.js")}
        self.assertIn(("compute", SymbolKind.FUNCTION), names)
        imports = [(i.module, i.symbol) for i in self.index.imports_of("utils/main.js")]
        self.assertIn(("./math", "add"), imports)
        self.assertIn(("./math", "multiply"), imports)

    def test_ts_symbols(self):
        self.index.build()
        syms = self.index.symbols_in_file("app/types.ts")
        by_name = {s.name: s.kind for s in syms}
        self.assertEqual(by_name["Shape"], SymbolKind.INTERFACE)
        self.assertEqual(by_name["Circle"], SymbolKind.CLASS)
        self.assertEqual(by_name["area"], SymbolKind.METHOD)
        self.assertEqual(by_name["describe"], SymbolKind.FUNCTION)

    def test_go_fallback_method_by_receiver(self):
        self.index.build()
        syms = self.index.symbols_in_file("svc/handler.go")
        by_name = {s.name: (s.kind, s.qualified_name) for s in syms}
        self.assertEqual(by_name["Handler"], (SymbolKind.CLASS, "svc.handler.Handler"))
        self.assertEqual(by_name["Handle"],
                         (SymbolKind.METHOD, "svc.handler.Handler.Handle"))
        self.assertEqual(by_name["helper"],
                         (SymbolKind.FUNCTION, "svc.handler.helper"))
        # go imports are recorded
        modules = [i.module for i in self.index.imports_of("svc/handler.go")]
        self.assertIn("fmt", modules)

    def test_cross_file_call_resolution_python(self):
        self.index.build()
        self.assertEqual(
            self.index.callers_of("pkg.core.helper"),
            [("pkg.core.consumer", "call", 1), ("pkg.service.run", "call", 1)])
        self.assertEqual(self.index.callees_of("pkg.core.consumer"),
                         [("pkg.core.helper", "call", 1)])
        # who calls INTO pkg/core.py from outside
        self.assertEqual(self.index.callers_of_file("pkg/core.py"),
                         [("pkg/core.py", 1), ("pkg/service.py", 1)])

    def test_cross_file_call_resolution_js(self):
        self.index.build()
        self.assertEqual(self.index.callees_of("utils.main.compute"),
                         [("utils.math.add", "call", 1),
                          ("utils.math.multiply", "call", 1)])
        self.assertEqual(self.index.callers_of_file("utils/math.js"),
                         [("utils/main.js", 2)])
        self.assertEqual(self.index.file_dependencies("utils/main.js"),
                         ["utils/math.js"])

    def test_unresolved_references_kept_honestly(self):
        self.index.build()
        unresolved = self.index.unresolved_references()
        self.assertIn("fmt.Println", unresolved)   # external library
        self.assertIn("c.area", unresolved)        # parameter-bound call

    def test_incremental_rebuild_reuses_state(self):
        first = self.index.build()
        self.assertEqual(first.parsed_files, 6)
        second = self.index.build()
        self.assertEqual(second.parsed_files, 0)
        self.assertTrue(second.incremental)

    def test_references_persist_through_store(self):
        self.index.build()
        db = Path(self._tmp.name) / "idx.db"
        self.index.save(db)
        fresh = RepositoryIndex(self.repo)
        self.assertTrue(fresh.load(db))
        self.assertEqual(fresh.callers_of("pkg.core.helper"),
                         self.index.callers_of("pkg.core.helper"))
        self.assertEqual(fresh.callees_of("utils.main.compute"),
                         self.index.callees_of("utils.main.compute"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
