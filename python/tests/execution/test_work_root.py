"""Thread-local work root tests — the tools.py binding that makes
parallel DAG task threads safe: file/shell tools resolve against the
calling thread's root, never the (process-global, shared) cwd.

Real files in tmp dirs; two real threads exercise the isolation."""

import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.tools import (  # noqa: E402
    _edit_file,
    _grep_search,
    _list_files,
    _read_file,
    _run_shell,
    _write_file,
    execute_tool,
    set_work_root,
)


class TestWorkRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "worktree"
        self.root.mkdir()
        set_work_root(str(self.root))

    def tearDown(self):
        set_work_root(None)

    def test_write_and_read_resolve_under_root(self):
        out = _write_file({"file_path": "sub/x.txt", "content": "hello\n"})
        self.assertIn("Successfully wrote", out)
        self.assertTrue((self.root / "sub" / "x.txt").exists())
        self.assertFalse(Path.cwd().joinpath("sub", "x.txt").exists())
        self.assertIn("1 | hello", _read_file({"file_path": "sub/x.txt"}))

    def test_edit_file_under_root(self):
        _write_file({"file_path": "a.py", "content": "x = 1\n"})
        out = _edit_file({"file_path": "a.py", "old_string": "x = 1",
                          "new_string": "x = 2"})
        self.assertIn("Successfully edited", out)
        self.assertEqual((self.root / "a.py").read_text(), "x = 2\n")

    def test_absolute_paths_pass_through(self):
        target = Path(self._tmp.name) / "outside.txt"
        _write_file({"file_path": str(target), "content": "outside\n"})
        self.assertTrue(target.exists())

    def test_shell_cwd_bound_to_root(self):
        out = _run_shell({"command": "pwd"})
        self.assertEqual(out.strip(), str(self.root))

    def test_grep_search_under_root(self):
        _write_file({"file_path": "findme.py", "content": "NEEDLE = 1\n"})
        out = _grep_search({"pattern": "NEEDLE"})
        self.assertIn("NEEDLE = 1", out)

    def test_list_files_under_root_returns_relative_paths(self):
        _write_file({"file_path": "pkg/util.py", "content": "pass\n"})
        out = _list_files({"path": "pkg", "pattern": "*.py"})
        self.assertIn("util.py", out)

    def test_unset_restores_cwd_behavior(self):
        set_work_root(None)
        rel = "workroot-probe.txt"
        try:
            _write_file({"file_path": rel, "content": "cwd\n"})
            self.assertTrue(Path.cwd().joinpath(rel).exists())
            self.assertFalse((self.root / rel).exists())
        finally:
            Path.cwd().joinpath(rel).unlink(missing_ok=True)

    def test_root_is_thread_local(self):
        """Two threads bound to different roots writing the SAME relative
        path must produce two different files — the property that makes
        parallel task worktrees safe."""
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        barrier = threading.Barrier(2)
        results = {}

        def worker(root, key):
            set_work_root(str(root))
            try:
                barrier.wait()
                _write_file({"file_path": "shared.txt", "content": f"{key}\n"})
                results[key] = (root / "shared.txt").read_text()
            finally:
                set_work_root(None)

        t1 = threading.Thread(target=worker, args=(self.root, "one"))
        t2 = threading.Thread(target=worker, args=(other, "two"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(results, {"one": "one\n", "two": "two\n"})
        self.assertEqual((self.root / "shared.txt").read_text(), "one\n")
        self.assertEqual((other / "shared.txt").read_text(), "two\n")


class TestWorkRootBookkeeping(unittest.IsolatedAsyncioTestCase):
    """The read-before-edit mtime bookkeeping in execute_tool must use the
    rooted path too, or writes would be wrongly refused."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "worktree"
        self.root.mkdir()
        set_work_root(str(self.root))

    async def asyncTearDown(self):
        set_work_root(None)

    async def test_read_before_edit_state_uses_rooted_path(self):
        state = {}
        (self.root / "a.py").write_text("v = 1\n")
        await execute_tool("read_file", {"file_path": "a.py"}, state)
        out = await execute_tool("write_file",
                                 {"file_path": "a.py", "content": "v = 2\n"},
                                 state)
        self.assertIn("Successfully wrote", out)
        # A file NOT read beforehand is still refused (the guard is alive).
        (self.root / "b.py").write_text("w = 1\n")
        refused = await execute_tool("write_file",
                                     {"file_path": "b.py", "content": "w = 2\n"},
                                     state)
        self.assertIn("must read this file before", refused)


if __name__ == "__main__":
    unittest.main(verbosity=2)
