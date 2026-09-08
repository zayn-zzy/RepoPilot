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


class TestParallelIsolation(unittest.TestCase):
    """The doc's core scenario: two no-dependency coding tasks run at the
    same time, in separate worktrees, touching different files — neither
    pollutes the other's filesystem state, and each has an independent
    git diff."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)
        self.wt1 = self.mgr.create("T001")
        self.wt2 = self.mgr.create("T002")

    def test_two_parallel_tasks_do_not_pollute_each_other(self):
        (self.wt1.path / "a.py").write_text("def a():\n    return 100  # task one\n")
        (self.wt2.path / "b.py").write_text("def b():\n    return 200  # task two\n")
        (self.wt1.path / "new_one.txt").write_text("only task one\n")

        # Filesystem isolation: task one's edits are invisible everywhere else.
        self.assertEqual((self.repo / "a.py").read_text(), "def a():\n    return 1\n")
        self.assertEqual((self.repo / "b.py").read_text(), "def b():\n    return 2\n")
        self.assertEqual((self.wt2.path / "a.py").read_text(), "def a():\n    return 1\n")
        self.assertFalse((self.repo / "new_one.txt").exists())
        self.assertFalse((self.wt2.path / "new_one.txt").exists())

        # Independent git diffs: each diff contains only its own files.
        d1 = self.mgr.diff("T001")
        d2 = self.mgr.diff("T002")
        self.assertEqual(d1.files, ["a.py"])
        self.assertEqual(d1.untracked, ["new_one.txt"])
        self.assertEqual(d2.files, ["b.py"])
        self.assertEqual(d2.untracked, [])
        self.assertIn("return 100  # task one", d1.patch)
        self.assertNotIn("task two", d1.patch)
        self.assertIn("return 200  # task two", d2.patch)
        self.assertNotIn("task one", d2.patch)

        # The main workspace stays clean throughout.
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(_git(self.wt1.path, "status", "--porcelain").count("\n") + 1, 2)
        self.assertEqual(_git(self.wt2.path, "status", "--porcelain").count("\n") + 1, 1)

    def test_commit_records_change_and_keeps_main_clean(self):
        (self.wt1.path / "a.py").write_text("def a():\n    return 100\n")
        hash1 = self.mgr.commit("T001", "feat: task one changes a.py")
        self.assertEqual(hash1, _git(self.wt1.path, "rev-parse", "HEAD"))
        self.assertNotEqual(hash1, self.wt1.base_commit)
        # The main branch did not move.
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), self.wt1.base_commit)
        # The committed change is part of the task diff.
        d = self.mgr.diff("T001")
        self.assertEqual(d.files, ["a.py"])
        self.assertIn("return 100", d.patch)
        self.assertEqual(self.mgr.status("T001").clean, True)
        # Committing again with no changes is an error, not a silent no-op.
        with self.assertRaises(NothingToCommitError):
            self.mgr.commit("T001", "nothing new")

    def test_untracked_files_visible_then_included_by_commit(self):
        # New (untracked) files belong to the task: diff() reports them,
        # commit() records them (git add -A), then they appear as tracked.
        (self.wt2.path / "scratch.py").write_text("print('wip')\n")
        self.assertEqual(self.mgr.diff("T002").untracked, ["scratch.py"])
        hash1 = self.mgr.commit("T002", "feat: add scratch module")
        self.assertNotEqual(hash1, self.wt2.base_commit)
        d = self.mgr.diff("T002")
        self.assertEqual(d.files, ["scratch.py"])
        self.assertEqual(d.untracked, [])
        self.assertIn("print('wip')", d.patch)
        # Only the task branch moved; main is untouched and still clean.
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), self.wt2.base_commit)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")


class TestMergeAndConflicts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)
        self.base = _git(self.repo, "rev-parse", "HEAD")

    def _task_change(self, task_id: str, file: str, content: str) -> str:
        wt = self.mgr.create(task_id)
        (wt.path / file).write_text(content)
        return self.mgr.commit(task_id, f"task {task_id} changes {file}")

    def test_merge_clean_merges_task_into_target(self):
        self._task_change("T001", "a.py", "def a():\n    return 100\n")
        result = self.mgr.merge("T001", "main")
        self.assertTrue(result.merged)
        self.assertEqual(result.target_branch, "main")
        # A --no-ff merge commit with two parents advanced main.
        head = _git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(result.commit, head)
        self.assertNotEqual(head, self.base)
        parents = _git(self.repo, "rev-list", "--parents", "-n1", "HEAD").split()
        self.assertEqual(len(parents), 3)  # commit + 2 parents
        # The task's change is now in the main workspace.
        self.assertIn("return 100", (self.repo / "a.py").read_text())
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_merge_conflict_never_overwrites(self):
        """禁止冲突时暴力覆盖代码: the same line edited on both sides must
        abort the merge and leave the main workspace byte-identical."""
        self._task_change("T001", "a.py", "def a():\n    return 100  # task one\n")
        # The main branch edits the same line independently.
        (self.repo / "a.py").write_text("def a():\n    return 200  # main side\n")
        _git(self.repo, "add", "a.py")
        _git(self.repo, "commit", "-qm", "main side edits a.py")
        main_head = _git(self.repo, "rev-parse", "HEAD")

        with self.assertRaises(WorktreeConflictError) as ctx:
            self.mgr.merge("T001", "main")
        self.assertEqual(ctx.exception.files, ["a.py"])
        # Nothing was overwritten: no conflict markers, main content intact,
        # main HEAD unmoved, workspace clean again.
        self.assertEqual((self.repo / "a.py").read_text(),
                         "def a():\n    return 200  # main side\n")
        self.assertNotIn("task one", (self.repo / "a.py").read_text())
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), main_head)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_merge_refuses_dirty_workspace(self):
        self._task_change("T001", "a.py", "def a():\n    return 100\n")
        (self.repo / "b.py").write_text("def b():\n    return 9\n")  # uncommitted
        with self.assertRaises(WorktreeDirtyError):
            self.mgr.merge("T001", "main")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), self.base)

    def test_merge_refuses_wrong_branch(self):
        self._task_change("T001", "a.py", "def a():\n    return 100\n")
        _git(self.repo, "checkout", "-q", "master")  # fixture's original branch
        with self.assertRaises(WorktreeError):
            self.mgr.merge("T001", "main")
        _git(self.repo, "checkout", "-q", "main")

    def test_detect_conflicts_reports_files_without_touching_workspace(self):
        self._task_change("T001", "a.py", "def a():\n    return 100  # task one\n")
        (self.repo / "a.py").write_text("def a():\n    return 200  # main side\n")
        _git(self.repo, "add", "a.py")
        _git(self.repo, "commit", "-qm", "main side edits a.py")
        main_head = _git(self.repo, "rev-parse", "HEAD")

        conflicts = self.mgr.detect_conflicts("T001", "main")
        self.assertEqual(conflicts, ["a.py"])
        # The probe changed nothing anywhere.
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), main_head)
        self.assertEqual((self.repo / "a.py").read_text(),
                         "def a():\n    return 200  # main side\n")
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(_git(self.repo, "worktree", "list").count("worktrees/"), 1)

        # A non-conflicting task probes clean.
        self._task_change("T002", "b.py", "def b():\n    return 200\n")
        self.assertEqual(self.mgr.detect_conflicts("T002", "main"), [])


class TestMergeIntoCwd(unittest.TestCase):
    """Phase 11: merge(task_id, target_branch, cwd=...) merges into a
    dedicated checkout (the DAG executor's integration worktree) — the
    main workspace is never switched, dirtied or written to."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)

    def test_merge_into_other_worktree_branch(self):
        integration = self.mgr.create("INT", require_clean=False)
        task = self.mgr.create("T001", require_clean=False)
        (task.path / "a.py").write_text("def a():\n    return 100\n")
        self.mgr.commit("T001", "task T001 changes a.py")

        result = self.mgr.merge("T001", integration.branch, cwd=integration.path)

        self.assertTrue(result.merged)
        self.assertEqual(result.target_branch, integration.branch)
        # The integration worktree advanced; the task change is there.
        self.assertIn("return 100", (integration.path / "a.py").read_text())
        # The main workspace was never touched: same branch, same HEAD,
        # original content, clean.
        self.assertEqual(_git(self.repo, "branch", "--show-current"), "main")
        self.assertIn("return 1", (self.repo / "a.py").read_text())
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_merge_conflict_in_other_worktree_never_overwrites(self):
        integration = self.mgr.create("INT", require_clean=False)
        task = self.mgr.create("T001", require_clean=False)
        (task.path / "a.py").write_text("def a():\n    return 100  # task\n")
        self.mgr.commit("T001", "task T001 edits a.py")
        # The integration branch edits the same line independently.
        (integration.path / "a.py").write_text("def a():\n    return 200  # int\n")
        _git(integration.path, "add", "a.py")
        _git(integration.path, "commit", "-qm", "integration side edits a.py")
        int_head = _git(integration.path, "rev-parse", "HEAD")

        with self.assertRaises(WorktreeConflictError) as ctx:
            self.mgr.merge("T001", integration.branch, cwd=integration.path)

        self.assertEqual(ctx.exception.files, ["a.py"])
        # Nothing was overwritten: integration worktree content intact,
        # HEAD unmoved, clean after the abort.
        self.assertEqual((integration.path / "a.py").read_text(),
                         "def a():\n    return 200  # int\n")
        self.assertEqual(_git(integration.path, "rev-parse", "HEAD"), int_head)
        self.assertEqual(_git(integration.path, "status", "--porcelain"), "")

    def test_merge_cwd_must_be_on_target_branch(self):
        task = self.mgr.create("T001", require_clean=False)
        (task.path / "a.py").write_text("def a():\n    return 100\n")
        self.mgr.commit("T001", "task T001 changes a.py")
        # The main workspace is on `main`, the task worktree on task/T001 —
        # merging into `main` with cwd=task worktree must refuse.
        with self.assertRaises(WorktreeError):
            self.mgr.merge("T001", "main", cwd=task.path)


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)

    def test_cleanup_removes_worktree_and_branch(self):
        wt = self.mgr.create("T001")
        (wt.path / "a.py").write_text("def a():\n    return 100\n")
        self.mgr.commit("T001", "feat: task one")
        self.mgr.merge("T001", "main")
        self.mgr.cleanup("T001")
        self.assertFalse(wt.path.exists())
        self.assertEqual(_git(self.repo, "branch", "--list", "task/T001"), "")
        self.assertEqual(self.mgr.list(), [])
        self.assertEqual(_git(self.repo, "worktree", "list").count("worktrees/"), 0)
        # The merged change survives cleanup (only the task branch moved on).
        self.assertIn("return 100", (self.repo / "a.py").read_text())

    def test_cleanup_refuses_dirty_worktree_without_force(self):
        wt = self.mgr.create("T001")
        (wt.path / "a.py").write_text("def a():\n    return 100\n")  # uncommitted
        with self.assertRaises(WorktreeDirtyError):
            self.mgr.cleanup("T001")
        # Nothing was discarded: the worktree and its change are still there.
        self.assertTrue(wt.path.exists())
        self.assertIn("return 100", (wt.path / "a.py").read_text())
        self.mgr.cleanup("T001", force=True)
        self.assertFalse(wt.path.exists())
        self.assertEqual(_git(self.repo, "branch", "--list", "task/T001"), "")

    def test_cleanup_refuses_unmerged_branch_without_force(self):
        wt = self.mgr.create("T001")
        (wt.path / "a.py").write_text("def a():\n    return 100\n")
        self.mgr.commit("T001", "feat: task one")  # never merged into main
        with self.assertRaises(WorktreeError):
            self.mgr.cleanup("T001")
        # Atomic refusal: both the worktree and the branch remain intact.
        self.assertTrue(wt.path.exists())
        self.assertIn("task/T001", _git(self.repo, "branch", "--list"))
        self.mgr.cleanup("T001", force=True)
        self.assertFalse(wt.path.exists())
        self.assertEqual(_git(self.repo, "branch", "--list", "task/T001"), "")

    def test_cleanup_all_removes_every_registered_task(self):
        self.mgr.create("T001")
        self.mgr.create("T002")
        removed = self.mgr.cleanup_all()
        self.assertEqual(removed, ["T001", "T002"])
        self.assertEqual(self.mgr.list(), [])
        self.assertEqual(_git(self.repo, "branch", "--list", "task/*"), "")

    def test_cleanup_survives_manually_deleted_worktree_dir(self):
        wt = self.mgr.create("T001")
        (wt.path / "a.py").write_text("def a():\n    return 100\n")
        self.mgr.commit("T001", "feat: task one")
        self.mgr.merge("T001", "main")
        _git(self.repo, "worktree", "remove", "--force", str(wt.path))  # by hand
        # Cleanup finishes the job: branch and metadata are still removed.
        self.mgr.cleanup("T001")
        self.assertEqual(_git(self.repo, "branch", "--list", "task/T001"), "")
        self.assertEqual(self.mgr.list(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
