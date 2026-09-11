"""RepositoryIndex tests — build stats, query API (find_symbol /
find_definition / file_of_symbol / imports / dependencies), incremental
updates driven by hash/mtime, and SQLite persistence roundtrip."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import PythonParser, RepositoryIndex, SymbolKind  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


class CountingParser(PythonParser):
    """Counts parse_file calls so incremental behavior is observable."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def parse_file(self, *args, **kwargs):
        self.calls += 1
        return super().parse_file(*args, **kwargs)


def copy_fixture(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    shutil.copytree(FIXTURE, root)
    return root


class TestBuildStats(unittest.TestCase):
    def test_fixture_build_numbers(self):
        idx = RepositoryIndex(FIXTURE)
        r = idx.build()
        self.assertEqual(r.files, 9)
        self.assertEqual(r.symbols, 24)
        self.assertEqual(r.imports, 16)
        self.assertEqual(r.syntax_errors, 1)
        self.assertEqual(r.parsed_files, 9)
        self.assertFalse(r.incremental)
        self.assertGreater(r.elapsed_s, 0)


class TestQueries(unittest.TestCase):
    def setUp(self):
        self.idx = RepositoryIndex(FIXTURE)
        self.idx.build()

    def test_find_symbol_by_name(self):
        matches = self.idx.find_symbol("greet")
        self.assertEqual([s.qualified_name for s in matches], ["pkg.models.User.greet"])

    def test_find_symbol_with_kind_filter(self):
        self.assertEqual(
            [s.qualified_name for s in self.idx.find_symbol("User", SymbolKind.CLASS)],
            ["pkg.models.User"],
        )
        self.assertEqual(
            self.idx.find_symbol("User", "method"), [],
        )

    def test_find_symbol_multiple_hits_sorted(self):
        hits = [s.qualified_name for s in self.idx.find_symbol("__init__")]
        self.assertEqual(hits, ["pkg.core.Processor.__init__", "pkg.models.User.__init__"])

    def test_find_definition_qualified(self):
        sym = self.idx.find_definition("pkg.core.Processor.Inner.inner_method")
        self.assertIsNotNone(sym)
        self.assertEqual(sym.file_path, str(FIXTURE / "pkg/core.py"))
        self.assertEqual(sym.location.start_line, 28)

    def test_find_definition_ambiguous_bare_name(self):
        self.assertIsNone(self.idx.find_definition("__init__"))  # 2 matches
        self.assertIsNotNone(self.idx.find_definition("process"))  # exactly 1

    def test_file_of_symbol(self):
        sym = self.idx.find_definition("pkg.utils.add")
        self.assertEqual(self.idx.file_of_symbol(sym), str(FIXTURE / "pkg/utils.py"))

    def test_symbols_in_file(self):
        self.assertEqual(len(self.idx.symbols_in_file("pkg/core.py")), 7)

    def test_imports_of(self):
        imps = self.idx.imports_of("pkg/sub/helper.py")
        self.assertEqual([(i.module, i.symbol) for i in imps],
                         [("utils", "add"), ("", "core")])

    def test_dependency_queries(self):
        self.assertIn("pkg/utils.py", self.idx.file_dependencies("pkg/core.py"))
        self.assertIn("pkg/models.py", self.idx.dependents("pkg/core.py"))
        self.assertIn("pkg.utils", self.idx.module_dependencies("pkg.sub.helper"))
        self.assertEqual(self.idx.file_of_module("pkg.models"), "pkg/models.py")

    def test_file_record(self):
        rec = self.idx.file_record("broken_syntax.py")
        self.assertIsNotNone(rec)
        self.assertTrue(rec.has_syntax_error)
        self.assertEqual(rec.module_name, "broken_syntax")


class TestIncremental(unittest.TestCase):
    def _build_with(self, root):
        parser = CountingParser()
        idx = RepositoryIndex(root, parser=parser)
        return idx, parser

    def test_second_build_parses_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, parser = self._build_with(copy_fixture(tmp))
            idx.build()
            self.assertEqual(parser.calls, 9)
            idx.build()
            self.assertEqual(parser.calls, 9, "unchanged tree must not re-parse")
            self.assertTrue(idx.build().incremental)

    def test_modified_file_reparsed_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture(tmp)
            idx, parser = self._build_with(root)
            idx.build()

            target = root / "pkg/utils.py"
            with target.open("a") as f:
                f.write("\n\ndef brand_new_func():\n    return 7\n")
            os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 10))

            r = idx.build()
            self.assertEqual(r.parsed_files, 1)
            self.assertEqual(parser.calls, 10)
            hits = idx.find_symbol("brand_new_func")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].qualified_name, "pkg.utils.brand_new_func")

    def test_deleted_file_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture(tmp)
            idx, parser = self._build_with(root)
            idx.build()

            (root / "broken_syntax.py").unlink()
            r = idx.build()
            self.assertEqual(r.removed_files, 1)
            self.assertEqual(r.syntax_errors, 0)
            self.assertNotIn("broken_syntax.py", idx.files())
            self.assertNotIn("broken_syntax.good_function",
                             [s.qualified_name for s in idx.find_symbol("good_function")])

    def test_modified_file_updates_dependency_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture(tmp)
            idx, parser = self._build_with(root)
            idx.build()

            # top_level.py adds a new import of pkg.utils
            target = root / "top_level.py"
            with target.open("a") as f:
                f.write("\nimport pkg.utils\n")
            os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 10))
            idx.build()

            self.assertIn("pkg/utils.py", idx.file_dependencies("top_level.py"))


class TestPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture(tmp)
            db = Path(tmp) / "index.db"

            idx = RepositoryIndex(root, db_path=db)
            r1 = idx.build(persist=True)
            idx.close()

            idx2 = RepositoryIndex(root, db_path=db)
            self.assertTrue(idx2.load())
            # Queries must match without re-parsing anything.
            self.assertEqual(len(idx2.files()), r1.files)
            self.assertEqual(len(idx2.find_symbol("greet")), 1)
            self.assertEqual(idx2.file_dependencies("pkg/core.py"),
                             ["pkg/models.py", "pkg/sub/helper.py", "pkg/utils.py"])
            r2 = idx2.build()   # incremental on top of the restored state
            self.assertEqual(r2.parsed_files, 0)
            idx2.close()

    def test_load_nonexistent_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = RepositoryIndex(copy_fixture(tmp), db_path=Path(tmp) / "nope.db")
            self.assertFalse(idx.load())
            idx.close()

    def test_load_db_of_different_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = copy_fixture(tmp)
            db = Path(tmp) / "shared.db"
            idx = RepositoryIndex(root_a, db_path=db)
            idx.build(persist=True)
            idx.close()

            other_root = Path(tmp) / "other"
            other_root.mkdir()
            idx2 = RepositoryIndex(other_root, db_path=db)
            self.assertFalse(idx2.load())  # root mismatch → fresh start
            idx2.close()

    def test_save_without_db_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = RepositoryIndex(copy_fixture(tmp))
            with self.assertRaises(ValueError):
                idx.save()


class TestLoadOrBuild(unittest.TestCase):
    """Phase 16 — load_or_build: the persisted index is actually reused
    (load + incremental build), saved back when it changes, and seeded
    into other checkouts of the same repo without ever writing back."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = copy_fixture(self._tmp.name)
        self.db = Path(self._tmp.name) / "index.db"

    def _edit(self, rel: str, text: str) -> None:
        target = self.root / rel
        with target.open("a") as f:
            f.write(text)
        st = target.stat()
        os.utime(target, (st.st_atime, st.st_mtime + 10))

    def test_fresh_build_saves_and_reports(self):
        idx = RepositoryIndex(self.root)
        result, note = idx.load_or_build(self.db)
        self.assertTrue(self.db.is_file())
        self.assertEqual(result.parsed_files, result.files)
        self.assertIn("no saved index — built fresh", note)
        idx.close()

    def test_second_run_is_loaded_and_up_to_date(self):
        idx = RepositoryIndex(self.root)
        idx.load_or_build(self.db)
        idx.close()
        idx2 = RepositoryIndex(self.root)
        result, note = idx2.load_or_build(self.db)
        self.assertEqual(result.parsed_files, 0)
        self.assertIn("loaded", note)
        self.assertIn("up to date", note)
        idx2.close()

    def test_changed_file_is_reparsed_and_saved_back(self):
        idx = RepositoryIndex(self.root)
        first = idx.load_or_build(self.db)[0]
        idx.close()
        self._edit("top_level.py", "\ndef symbol_added_after_indexing():\n"
                   "    return 42\n")
        idx2 = RepositoryIndex(self.root)
        result, note = idx2.load_or_build(self.db)
        self.assertEqual(result.parsed_files, 1)
        self.assertIn("re-parsed 1 changed file(s)", note)
        self.assertIn("saved", note)
        # the new symbol is visible immediately (the save-back persisted it)
        self.assertTrue(idx2.find_symbol("symbol_added_after_indexing"))
        self.assertNotEqual(first.symbols, result.symbols)
        idx2.close()

    def test_seeded_other_root_relocates_and_never_writes_back(self):
        main = RepositoryIndex(self.root)
        main.load_or_build(self.db)
        main.close()
        before = self.db.read_bytes()

        other = Path(self._tmp.name) / "other"
        shutil.copytree(self.root, other)
        idx = RepositoryIndex(other)
        result, note = idx.load_or_build(self.db, allow_other_root=True,
                                         save_back=False)
        self.assertEqual(result.parsed_files, 0)   # content identical
        self.assertIn("seeded from", note)
        self.assertIn("main repo index", note)
        sym = idx.find_definition("pkg.models.User.greet")
        self.assertIsNotNone(sym)
        self.assertTrue(str(sym.location.file_path).startswith(str(other)))
        self.assertEqual(self.db.read_bytes(), before)  # main db untouched
        idx.close()

    def test_seeded_index_is_never_saved_back_even_with_save_back(self):
        main = RepositoryIndex(self.root)
        main.load_or_build(self.db)
        main.close()
        before = self.db.read_bytes()
        other = Path(self._tmp.name) / "other"
        shutil.copytree(self.root, other)
        idx = RepositoryIndex(other)
        _, note = idx.load_or_build(self.db, allow_other_root=True,
                                    save_back=True)   # must still refuse
        self.assertIn("seeded from", note)
        self.assertEqual(self.db.read_bytes(), before)
        idx.close()

    def test_foreign_db_is_replaced_with_an_honest_note(self):
        # a db file that belongs to a DIFFERENT repo (not just another
        # checkout) must not be loaded — it is rebuilt and replaced.
        other_root = Path(self._tmp.name) / "otherrepo"
        shutil.copytree(FIXTURE, other_root)
        db2 = Path(self._tmp.name) / "foreign.db"
        idx = RepositoryIndex(other_root)
        idx.load_or_build(db2)
        idx.close()
        shutil.copy2(db2, self.db)
        idx2 = RepositoryIndex(self.root)
        _, note = idx2.load_or_build(self.db)
        self.assertIn("replaced an index from a different root", note)
        idx2.close()

    def test_full_rebuild_ignores_saved_index(self):
        idx = RepositoryIndex(self.root)
        first = idx.load_or_build(self.db)[0]
        idx.close()
        idx2 = RepositoryIndex(self.root)
        result, note = idx2.load_or_build(self.db, full=True)
        self.assertEqual(result.parsed_files, result.files)  # everything re-parsed
        self.assertIn("full rebuild", note)
        self.assertEqual(result.symbols, first.symbols)
        idx2.close()

    def test_load_other_root_without_allow_is_refused(self):
        main = RepositoryIndex(self.root)
        main.load_or_build(self.db)
        main.close()
        other = Path(self._tmp.name) / "other"
        shutil.copytree(self.root, other)
        idx = RepositoryIndex(other)
        self.assertFalse(idx.load(self.db))          # default: refuse
        self.assertTrue(idx.load(self.db, allow_other_root=True))
        idx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
