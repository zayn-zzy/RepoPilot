"""PythonParser tests — function/class/method extraction, nesting, async,
signatures/docstrings, import forms, and syntax-error tolerance."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import PythonParser, SymbolKind  # noqa: E402
from mini_claude.repo.scanner import path_to_module  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


class ParserTestBase(unittest.TestCase):
    def setUp(self):
        self.parser = PythonParser()

    def parse(self, name):
        return self.parser.parse_file(FIXTURE / name, module_name=path_to_module(name))

    def syms(self, mod, kind=None):
        out = {s.qualified_name: s for s in mod.symbols}
        if kind is not None:
            out = {k: v for k, v in out.items() if v.kind is kind}
        return out


class TestFunctionExtraction(ParserTestBase):
    def test_module_functions(self):
        mod = self.parse("pkg/utils.py")
        syms = self.syms(mod, SymbolKind.FUNCTION)
        self.assertEqual(set(syms), {"pkg.utils.add", "pkg.utils.async_fetch",
                                     "pkg.utils.outer", "pkg.utils.outer.inner"})
        self.assertEqual(syms["pkg.utils.add"].location.start_line, 5)
        self.assertEqual(syms["pkg.utils.add"].signature, "def add(a, b)")
        self.assertEqual(syms["pkg.utils.add"].docstring, "Add two numbers.")

    def test_async_function_signature(self):
        mod = self.parse("pkg/utils.py")
        sig = self.syms(mod)["pkg.utils.async_fetch"].signature
        self.assertTrue(sig.startswith("async def"))

    def test_nested_function(self):
        mod = self.parse("pkg/utils.py")
        inner = self.syms(mod)["pkg.utils.outer.inner"]
        self.assertEqual(inner.kind, SymbolKind.FUNCTION)
        self.assertEqual(inner.docstring, "Nested helper.")


class TestClassExtraction(ParserTestBase):
    def test_classes_and_inheritance(self):
        mod = self.parse("pkg/models.py")
        syms = self.syms(mod, SymbolKind.CLASS)
        self.assertIn("pkg.models.User", syms)
        self.assertEqual(syms["pkg.models.User"].signature, "class User(BaseModel)")
        self.assertEqual(syms["pkg.models.User"].docstring, "A user model.")

    def test_methods(self):
        mod = self.parse("pkg/models.py")
        methods = self.syms(mod, SymbolKind.METHOD)
        self.assertIn("pkg.models.User.greet", methods)
        self.assertIn("pkg.models.BaseModel.to_dict", methods)
        self.assertNotIn("pkg.models.User", methods)  # the class itself

    def test_nested_class_and_its_method(self):
        mod = self.parse("pkg/models.py")
        syms = self.syms(mod)
        self.assertEqual(syms["pkg.models.User.Address"].kind, SymbolKind.CLASS)
        self.assertEqual(syms["pkg.models.User.Address.label"].kind, SymbolKind.METHOD)

    def test_method_line_numbers(self):
        mod = self.parse("pkg/models.py")
        greet = self.syms(mod)["pkg.models.User.greet"]
        self.assertEqual((greet.location.start_line, greet.location.end_line), (18, 19))

    def test_async_method(self):
        mod = self.parse("pkg/core.py")
        run = self.syms(mod)["pkg.core.Processor.run_async"]
        self.assertEqual(run.kind, SymbolKind.METHOD)
        self.assertTrue(run.signature.startswith("async def"))


class TestImportExtraction(ParserTestBase):
    def test_plain_imports(self):
        mod = self.parse("pkg/core.py")
        plain = [i for i in mod.imports if i.symbol is None]
        # only `import os` is a plain import here; `from typing import ...`
        # is a from-import and must not leak its module node as a symbol
        self.assertEqual({i.module for i in plain}, {"os"})

    def test_from_import_with_symbols(self):
        mod = self.parse("pkg/core.py")
        froms = [i for i in mod.imports if i.symbol is not None]
        by_symbol = {i.symbol: i for i in froms}
        self.assertEqual(by_symbol["add"].module, "utils")
        self.assertEqual(by_symbol["add"].level, 1)
        self.assertEqual(by_symbol["nested_helper"].module, "sub.helper")
        self.assertEqual(by_symbol["BaseModel"].module, "pkg.models")
        self.assertEqual(by_symbol["BaseModel"].level, 0)

    def test_relative_import_alias(self):
        mod = self.parse("pkg/sub/helper.py")
        add = [i for i in mod.imports if i.symbol == "add"][0]
        self.assertEqual(add.module, "utils")
        self.assertEqual(add.alias, "add_alias")
        self.assertEqual(add.level, 2)

    def test_relative_package_import(self):
        mod = self.parse("pkg/sub/helper.py")
        core = [i for i in mod.imports if i.symbol == "core"][0]
        self.assertEqual(core.module, "")
        self.assertEqual(core.level, 2)

    def test_import_linenos(self):
        mod = self.parse("pkg/core.py")
        for i in mod.imports:
            self.assertGreater(i.lineno, 0)

    def test_function_level_imports_not_collected(self):
        """Imports inside function bodies belong to runtime, not the module
        dependency surface."""
        with tempfile_module("def f():\n    import math\n    return math.pi\n") as p:
            mod = self.parser.parse_file(p)
            self.assertEqual(mod.imports, [])
            self.assertEqual(len(mod.symbols), 1)


class TestSyntaxEdgeCases(ParserTestBase):
    def test_broken_file_flagged_and_partial(self):
        mod = self.parse("broken_syntax.py")
        self.assertTrue(mod.has_syntax_error)
        names = {s.name for s in mod.symbols}
        self.assertIn("good_function", names)   # valid region still extracted

    def test_clean_file_not_flagged(self):
        mod = self.parse("pkg/utils.py")
        self.assertFalse(mod.has_syntax_error)

    def test_non_module_filename(self):
        mod = self.parse("weird-name.py")
        self.assertIsNone(mod.module_name)
        self.assertEqual(mod.symbols[0].qualified_name, "dashed_file_function")

    def test_content_hash_stable(self):
        a = self.parse("pkg/utils.py")
        b = self.parse("pkg/utils.py")
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertEqual(len(a.content_hash), 64)

    def test_multibyte_utf8_content(self):
        """tree-sitter offsets are BYTE offsets — a decoded-str slice would
        misalign on box-drawing / CJK content and corrupt symbol names."""
        content = (
            "# ─── 中文注释 ───\n"
            "VALUE = '日本語'\n"
            "def after_unicode():\n"
            "    return VALUE\n"
            "class 箱子:\n"
            "    pass\n"
        )
        with tempfile_module(content) as p:
            mod = self.parser.parse_file(p, module_name="m")
        self.assertFalse(mod.has_syntax_error)
        names = {s.name for s in mod.symbols}
        self.assertEqual(names, {"after_unicode", "箱子"})


def tempfile_module(content):
    import contextlib
    import tempfile

    @contextlib.contextmanager
    def _ctx():
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.py"
            p.write_text(content)
            yield p

    return _ctx()


if __name__ == "__main__":
    unittest.main(verbosity=2)
