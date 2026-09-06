"""RepositoryScanner tests — ignore rules, file discovery, module-name
mapping, large-file and generated-file exclusion."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryScanner, path_to_module  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


class TestPathToModule(unittest.TestCase):
    def test_module_file(self):
        self.assertEqual(path_to_module("pkg/sub/helper.py"), "pkg.sub.helper")

    def test_init_file(self):
        self.assertEqual(path_to_module("pkg/__init__.py"), "pkg")

    def test_non_identifier_returns_none(self):
        self.assertIsNone(path_to_module("weird-name.py"))
        self.assertIsNone(path_to_module("pkg/123bad.py"))

    def test_non_python_returns_none(self):
        self.assertIsNone(path_to_module("README.md"))


class TestScannerOnFixture(unittest.TestCase):
    def setUp(self):
        self.scanner = RepositoryScanner(FIXTURE)

    def test_scans_expected_python_files(self):
        files = {str(p) for p in self.scanner.scan()}
        self.assertEqual(files, {
            "broken_syntax.py",
            "pkg/__init__.py",
            "pkg/core.py",
            "pkg/models.py",
            "pkg/sub/__init__.py",
            "pkg/sub/helper.py",
            "pkg/utils.py",
            "top_level.py",
            "weird-name.py",
        })

    def test_ignored_directories_never_appear(self):
        files = {str(p) for p in self.scanner.scan()}
        for excluded in (
            "pkg/vendor/vendored.py",     # vendor/
            "node_modules/fake.py",        # node_modules/
            ".venv/lib/venv_mod.py",       # .venv/
            "__pycache__/stale.py",        # __pycache__/
        ):
            self.assertNotIn(excluded, files, f"{excluded} must be ignored")

    def test_generated_file_ignored(self):
        files = {str(p) for p in self.scanner.scan()}
        self.assertNotIn("pkg/generated_code.py", files)


class TestScannerEdgeCases(unittest.TestCase):
    def test_hidden_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".hidden").mkdir()
            (root / ".hidden" / "x.py").write_text("def f(): pass")
            (root / "ok.py").write_text("def g(): pass")
            files = {str(p) for p in RepositoryScanner(root).scan()}
            self.assertEqual(files, {"ok.py"})

    def test_large_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "big.py").write_text("x" * 5000)
            (root / "small.py").write_text("def f(): pass")
            files = {str(p) for p in RepositoryScanner(root, max_file_size=1000).scan()}
            self.assertEqual(files, {"small.py"})

    def test_custom_ignore_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "third_party").mkdir()
            (root / "third_party" / "a.py").write_text("def f(): pass")
            (root / "main.py").write_text("def g(): pass")
            files = {str(p) for p in RepositoryScanner(root, ignore_dirs={"third_party"}).scan()}
            self.assertEqual(files, {"main.py"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
