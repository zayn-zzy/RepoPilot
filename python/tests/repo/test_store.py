"""SQLiteStore tests — save/load roundtrip for files, symbols, and imports."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import SQLiteStore, SymbolKind  # noqa: E402
from mini_claude.repo.symbols import (  # noqa: E402
    FileRecord,
    ImportInfo,
    Location,
    Reference,
    Symbol,
)


ROOT = "/repo/root"


def _sample_state():
    """Production symbol/import file_paths are absolute; records stay
    repo-relative. The store must normalize locations to relative on save
    and re-absolutize them on load."""
    abs_mod = f"{ROOT}/pkg/mod.py"
    abs_broken = f"{ROOT}/broken.py"
    files = {
        "pkg/mod.py": FileRecord("pkg/mod.py", "pkg.mod", "a" * 64, 1234.5, 100, False),
        "broken.py": FileRecord("broken.py", "broken", "b" * 64, 1234.5, 50, True),
    }
    symbols = {
        "pkg/mod.py": [
            Symbol("run", SymbolKind.FUNCTION, Location(abs_mod, 1, 3),
                   "pkg.mod.run", "def run()", "Doc here."),
            Symbol("Runner", SymbolKind.CLASS, Location(abs_mod, 5, 9),
                   "pkg.mod.Runner", "class Runner"),
        ],
        "broken.py": [],
    }
    imports = {
        "pkg/mod.py": [
            ImportInfo(abs_mod, "os"),
            ImportInfo(abs_mod, "utils", symbol="add", alias="a", level=1, lineno=2),
        ],
        "broken.py": [],
    }
    references = {
        "pkg/mod.py": [
            Reference(abs_mod, "pkg.mod.run", "utils.add", "call", lineno=2),
            Reference(abs_mod, "pkg.mod.run", "os.path.join", "attribute", lineno=3),
        ],
        "broken.py": [],
    }
    return files, symbols, imports, references


class TestStoreRoundtrip(unittest.TestCase):
    def test_save_load(self):
        files, symbols, imports, references = _sample_state()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "idx.db"
            store = SQLiteStore(db)
            store.save("/repo/root", files, symbols, imports, references)
            store.close()

            store = SQLiteStore(db)
            loaded = store.load()
            store.close()

        self.assertIsNotNone(loaded)
        root, l_files, l_symbols, l_imports, l_refs = loaded
        self.assertEqual(root, "/repo/root")
        self.assertEqual(set(l_files), set(files))
        self.assertEqual(l_files["broken.py"].has_syntax_error, True)
        self.assertEqual(l_files["pkg/mod.py"].content_hash, "a" * 64)

        syms = l_symbols["pkg/mod.py"]
        self.assertEqual(len(syms), 2)
        self.assertEqual(syms[0].name, "run")
        self.assertEqual(syms[0].kind, SymbolKind.FUNCTION)
        # References roundtrip too (Phase 13): relative on disk, absolute
        # file paths back in memory.
        refs = l_refs["pkg/mod.py"]
        self.assertEqual([(r.caller, r.target, r.kind, r.lineno) for r in refs],
                         [("pkg.mod.run", "utils.add", "call", 2),
                          ("pkg.mod.run", "os.path.join", "attribute", 3)])
        self.assertEqual(refs[0].file_path, f"{ROOT}/pkg/mod.py")
        self.assertEqual(syms[0].qualified_name, "pkg.mod.run")
        self.assertEqual(syms[0].docstring, "Doc here.")
        self.assertEqual((syms[0].location.start_line, syms[0].location.end_line), (1, 3))

        imps = l_imports["pkg/mod.py"]
        self.assertEqual(len(imps), 2)
        self.assertEqual(imps[1].module, "utils")
        self.assertEqual(imps[1].symbol, "add")
        self.assertEqual(imps[1].alias, "a")
        self.assertEqual(imps[1].level, 1)
        self.assertEqual(imps[1].lineno, 2)

    def test_load_empty_db_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "empty.db")
            self.assertIsNone(store.load())
            store.close()

    def test_save_overwrites_previous(self):
        files, symbols, imports, references = _sample_state()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "idx.db"
            store = SQLiteStore(db)
            store.save("/r", files, symbols, imports, references)
            store.save("/r", {}, {}, {}, {})   # replace with empty state
            loaded = store.load()
            store.close()
            self.assertEqual(loaded[1], {})
            self.assertEqual(loaded[2], {})
            self.assertEqual(loaded[3], {})
            self.assertEqual(loaded[4], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
