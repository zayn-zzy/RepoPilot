"""DependencyGraph tests — module resolution (absolute/relative/aliased),
file/module dependency queries, closure, cycle detection, topological order."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.repo.symbols import ImportInfo  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


class GraphTestBase(unittest.TestCase):
    def setUp(self):
        self.idx = RepositoryIndex(FIXTURE)
        self.idx.build()
        self.g = self.idx.graph


class TestModuleMapping(GraphTestBase):
    def test_file_of_module(self):
        self.assertEqual(self.g.file_of_module("pkg.core"), "pkg/core.py")
        self.assertEqual(self.g.file_of_module("pkg"), "pkg/__init__.py")

    def test_module_of_file(self):
        self.assertEqual(self.g.module_of_file("pkg/utils.py"), "pkg.utils")
        self.assertIsNone(self.g.module_of_file("weird-name.py"))

    def test_external_module_not_mapped(self):
        self.assertIsNone(self.g.file_of_module("os"))


class TestDependencies(GraphTestBase):
    def test_file_dependencies(self):
        self.assertEqual(self.idx.file_dependencies("pkg/core.py"),
                         ["pkg/models.py", "pkg/sub/helper.py", "pkg/utils.py"])
        self.assertEqual(self.idx.file_dependencies("top_level.py"),
                         ["pkg/__init__.py", "pkg/core.py", "pkg/sub/helper.py"])

    def test_relative_import_from_init(self):
        # `from .core import Processor` in pkg/__init__.py resolves inside pkg
        self.assertEqual(self.idx.file_dependencies("pkg/__init__.py"),
                         ["pkg/core.py", "pkg/models.py"])

    def test_two_level_relative_import(self):
        # helper.py: `from ..utils import add` and `from .. import core`
        deps = self.idx.file_dependencies("pkg/sub/helper.py")
        self.assertEqual(deps, ["pkg/__init__.py", "pkg/utils.py"])

    def test_dependents(self):
        self.assertEqual(self.idx.dependents("pkg/core.py"),
                         ["pkg/__init__.py", "pkg/models.py", "top_level.py"])

    def test_importers_of_module(self):
        self.assertEqual(self.idx.importers_of("pkg.core"),
                         ["pkg", "pkg.models", "top_level"])

    def test_module_dependencies_include_external(self):
        deps = self.idx.module_dependencies("pkg.core")
        self.assertIn("os", deps)
        self.assertIn("typing", deps)
        self.assertIn("pkg.utils", deps)

    def test_closure(self):
        closure = self.g.closure("top_level.py")
        self.assertIn("pkg/core.py", closure)
        self.assertIn("pkg/utils.py", closure)   # transitive via core


class TestCycleDetection(GraphTestBase):
    def test_fixture_has_real_cycle(self):
        # pkg/core.py <-> pkg/models.py import each other (and form a larger
        # loop with pkg/__init__.py via sub/helper.py), so find_cycle returns
        # one of several valid cycles — check membership and the direct
        # two-file loop instead of one specific cycle.
        self.assertTrue(self.g.has_cycle())
        cycle = self.g.find_cycle()
        self.assertIsNotNone(cycle)
        cycle_nodes = {n for e in cycle for n in e}
        self.assertLessEqual(
            cycle_nodes,
            {"pkg/core.py", "pkg/models.py", "pkg/sub/helper.py", "pkg/__init__.py"},
        )
        self.assertIn("pkg/core.py", cycle_nodes)
        # The direct mutual import exists as edges in both directions.
        self.assertTrue(self.g._file_graph.has_edge("pkg/core.py", "pkg/models.py"))
        self.assertTrue(self.g._file_graph.has_edge("pkg/models.py", "pkg/core.py"))

    def test_topological_order_raises_on_cycle(self):
        with self.assertRaises(ValueError):
            self.g.topological_order()


class TestTopologicalOrder(unittest.TestCase):
    def test_acyclic_repo_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "c.py").write_text("import a\nimport b\n")
            (root / "b.py").write_text("import a\n")
            (root / "a.py").write_text("")
            idx = RepositoryIndex(root)
            idx.build()
            order = idx.graph.topological_order()
            self.assertEqual(set(order), {"a.py", "b.py", "c.py"})
            self.assertEqual(order[0], "a.py")          # no dependencies first
            self.assertLess(order.index("a.py"), order.index("b.py"))
            self.assertLess(order.index("b.py"), order.index("c.py"))

    def test_self_import_detected_as_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.py").write_text("import x\n")
            idx = RepositoryIndex(root)
            idx.build()
            self.assertTrue(idx.graph.has_cycle())


class TestImportResolution(unittest.TestCase):
    """Unit-level resolution without a full index build."""

    def _resolve(self, from_module, from_file, imp):
        from mini_claude.repo import DependencyGraph

        g = DependencyGraph()
        g.add_file(from_file, from_module)
        g.add_file("pkg/utils.py", "pkg.utils")
        g.add_file("pkg/__init__.py", "pkg")
        return g._resolve_module_name(from_file, imp)

    def test_absolute(self):
        self.assertEqual(
            self._resolve("pkg.sub.helper", "pkg/sub/helper.py",
                          ImportInfo("pkg/sub/helper.py", "pkg.utils")),
            "pkg.utils",
        )

    def test_level1_from_package_init(self):
        self.assertEqual(
            self._resolve("pkg", "pkg/__init__.py",
                          ImportInfo("pkg/__init__.py", "core", level=1)),
            "pkg.core",
        )

    def test_level1_from_module(self):
        self.assertEqual(
            self._resolve("pkg.sub.helper", "pkg/sub/helper.py",
                          ImportInfo("pkg/sub/helper.py", "peer", level=1)),
            "pkg.sub.peer",
        )

    def test_level2_from_module(self):
        self.assertEqual(
            self._resolve("pkg.sub.helper", "pkg/sub/helper.py",
                          ImportInfo("pkg/sub/helper.py", "utils", level=2)),
            "pkg.utils",
        )

    def test_level_beyond_top_level(self):
        self.assertIsNone(
            self._resolve("pkg", "pkg/__init__.py",
                          ImportInfo("pkg/__init__.py", "x", level=2)),
        )

    def test_no_module_name_fallback(self):
        from mini_claude.repo import DependencyGraph

        g = DependencyGraph()
        g.add_file("weird-name.py", None)
        self.assertIsNone(g._resolve_module_name("weird-name.py",
                                                 ImportInfo("weird-name.py", "x", level=1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
