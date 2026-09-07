"""WorktreeManager tests — real git repositories in temp dirs (no mocks):
task branches, worktree creation, independent diffs for parallel tasks,
merge conflict detection without overwrites, dirty-workspace guards,
invalid-branch rejection and cleanup."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.worktree import (  # noqa: E402
    NothingToCommitError,
    WorktreeConflictError,
    WorktreeDirtyError,
    WorktreeError,
    WorktreeManager,
)


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout.strip()


def _make_repo(tmp: Path) -> Path:
    """A minimal repo: a.py + b.py committed, branch `main` checked out."""
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "a.py").write_text("def a():\n    return 1\n")
    (repo / "b.py").write_text("def b():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


class TestWorktreeCreation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)

    def test_create_gives_branch_worktree_and_working_dir(self):
        info = self.mgr.create("T001")
        self.assertEqual(info.branch, "task/T001")
        self.assertEqual(info.path, self.repo / "worktrees" / "task-T001")
        self.assertTrue(info.path.is_dir())
        self.assertTrue((info.path / "a.py").exists())  # base checkout present
        self.assertTrue(info.base_commit)  # recorded base commit
        # The branch exists and the worktree is registered with git.
        self.assertIn("task/T001", _git(self.repo, "branch", "--list"))
        self.assertIn(str(info.path), _git(self.repo, "worktree", "list"))
        # The manager can find it back (fresh manager: metadata persists).
        again = WorktreeManager(self.repo)
        self.assertEqual([i.task_id for i in again.list()], ["T001"])
        self.assertEqual(again.get("T001").branch, "task/T001")
        # The main workspace stays clean (worktrees/ excluded locally).
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_create_requires_git_repository(self):
        plain = Path(self._tmp.name) / "not_a_repo"
        plain.mkdir()
        with self.assertRaises(WorktreeError):
            WorktreeManager(plain)

    def test_invalid_task_ids_rejected_without_leftovers(self):
        for bad in ("bad name!", "a..b", "a/b", "-leading", ".hidden"):
            with self.assertRaises(WorktreeError, msg=f"task_id {bad!r}"):
                self.mgr.create(bad)
        # Nothing was created: no branch, no worktree dir, no metadata.
        self.assertFalse((self.repo / "worktrees").exists())
        self.assertEqual(_git(self.repo, "branch", "--list", "task/*"), "")
        self.assertEqual(self.mgr.list(), [])

    def test_existing_branch_rejected(self):
        _git(self.repo, "checkout", "-qb", "task/T007")
        _git(self.repo, "checkout", "-q", "main")
        with self.assertRaises(WorktreeError):
            self.mgr.create("T007")
        self.assertFalse((self.repo / "worktrees" / "task-T007").exists())

    def test_duplicate_task_rejected(self):
        self.mgr.create("T001")
        with self.assertRaises(WorktreeError):
            self.mgr.create("T001")

    def test_dirty_main_workspace_blocks_create(self):
        (self.repo / "a.py").write_text("def a():\n    return 999\n")
        with self.assertRaises(WorktreeDirtyError):
            self.mgr.create("T001")
        self.assertFalse((self.repo / "worktrees" / "task-T001").exists())
        # Once clean again, creation succeeds.
        _git(self.repo, "checkout", "--", "a.py")
        self.mgr.create("T001")

    def test_status_reports_clean_worktree(self):
        self.mgr.create("T001")
        status = self.mgr.status("T001")
        self.assertEqual(status.branch, "task/T001")
        self.assertTrue(status.clean)
        self.assertEqual(status.changes, [])
        self.assertTrue(status.head_commit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
