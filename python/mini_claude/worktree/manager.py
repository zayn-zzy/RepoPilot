"""WorktreeManager — per-task git worktree isolation.

Each coding task gets its own branch, its own worktree (under
``<repo>/worktrees/task-<id>/``), its own working directory and its own
git diff, so parallel tasks can never pollute each other's filesystem
state::

    worktrees/
    ├── task-T001/
    ├── task-T002/
    └── task-T003/

The manager lives in the main worktree and is the only code path that
creates, merges or removes task worktrees. The ``worktrees/`` directory is
excluded from the main workspace via ``.git/info/exclude`` (a local,
untracked config file — the repository's tracked files are never touched),
so the main workspace stays clean while tasks are running.

Safety rules (禁止冲突时暴力覆盖代码):

- ``merge()`` never force-overwrites. On conflict it aborts the merge and
  raises WorktreeConflictError, leaving the main workspace byte-identical
  to its pre-merge state (verified with a clean-status assertion).
- ``cleanup()`` never force-deletes by default. A dirty worktree or a
  branch with unmerged commits blocks cleanup; discarding either requires
  an explicit ``force=True``.
- Only worktrees registered in this manager's metadata (under the repo's
  ``.git/repopilot/worktrees/``) are ever touched by cleanup.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

WORKTREES_DIR_DEFAULT = "worktrees"
TASK_BRANCH_PREFIX = "task/"
# Task ids become branch names (task/<id>) — conservative by design: no
# whitespace, no slashes, no ref metacharacters, no "..".  git
# check-ref-format is still the authoritative backstop in branch_name().
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorktreeError(Exception):
    """Base error for worktree operations."""


class WorktreeDirtyError(WorktreeError):
    """A workspace or worktree has uncommitted changes that would be lost."""


class WorktreeConflictError(WorktreeError):
    """A merge conflicted; nothing was overwritten (the merge was aborted).

    ``files`` lists the conflicted paths detected at abort time."""

    def __init__(self, files: list[str], message: str = ""):
        self.files = files
        super().__init__(message or f"merge conflicts in: {', '.join(files) or 'unknown files'}")


class NothingToCommitError(WorktreeError):
    """commit() found no changes to record."""


@dataclass
class WorktreeInfo:
    """A registered task worktree."""

    task_id: str
    branch: str
    path: Path
    base_commit: str
    created_at: str = ""


@dataclass
class WorktreeStatus:
    """Live status of one task worktree."""

    branch: str
    head_commit: str
    clean: bool
    changes: list[str] = field(default_factory=list)  # raw `git status --porcelain` lines


@dataclass
class TaskDiff:
    """The task's independent diff — everything the worktree changed since
    its base commit, in its own working directory only."""

    task_id: str
    branch: str
    base_commit: str
    head_commit: str
    files: list[str] = field(default_factory=list)      # tracked files changed vs base
    untracked: list[str] = field(default_factory=list)  # new files not yet committed
    patch: str = ""
    stat: str = ""

    @property
    def all_files(self) -> list[str]:
        return self.files + self.untracked


@dataclass
class MergeResult:
    """Outcome of merge() — on success the target branch advanced."""

    task_id: str
    target_branch: str
    merged: bool
    commit: str = ""
    conflicts: list[str] = field(default_factory=list)


class WorktreeManager:
    """Creates and manages per-task git worktrees inside one repository."""

    def __init__(self, root: str | Path, *, worktrees_dir: str = WORKTREES_DIR_DEFAULT):
        self.root = Path(root).resolve()
        top = self._run(["rev-parse", "--show-toplevel"], self.root)
        if top.returncode != 0:
            raise WorktreeError(
                f"{self.root} is not a git repository: {top.stderr.strip()}")
        # Normalize to the repository top level; git commands run from there.
        self.root = Path(top.stdout.strip())
        self.worktrees_root = self.root / worktrees_dir
        git_dir = self._run(["rev-parse", "--absolute-git-dir"], self.root)
        if git_dir.returncode != 0:
            raise WorktreeError(f"cannot locate git dir: {git_dir.stderr.strip()}")
        self._git_dir = Path(git_dir.stdout.strip())
        # Task metadata lives in the repo's own .git (never in any worktree,
        # so it cannot show up in a task diff).
        self._meta_dir = self._git_dir / "repopilot" / "worktrees"
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_excluded()

    # ─── git plumbing ───────────────────────────────────────────

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120,
        )

    def _git(self, args: list[str], cwd: Path) -> str:
        """Run git and return stdout; raise WorktreeError on failure."""
        p = self._run(args, cwd)
        if p.returncode != 0:
            detail = (p.stderr or p.stdout).strip()
            raise WorktreeError(
                f"git {' '.join(args)} failed (exit {p.returncode}): {detail}")
        return p.stdout.strip()

    def _git_ok(self, args: list[str], cwd: Path) -> bool:
        return self._run(args, cwd).returncode == 0

    def _current_branch(self) -> str:
        p = self._run(["symbolic-ref", "--short", "HEAD"], self.root)
        return p.stdout.strip() if p.returncode == 0 else "HEAD"

    def _assert_clean(self, cwd: Path, what: str) -> None:
        out = self._git(["status", "--porcelain"], cwd)
        if out.strip():
            raise WorktreeDirtyError(
                f"{what} has uncommitted changes; commit or stash them first:\n{out[:800]}")

    def _ensure_excluded(self) -> None:
        """Keep the main workspace clean: exclude the worktrees directory
        via .git/info/exclude (local-only, never touches tracked files)."""
        exclude = self._git_dir / "info" / "exclude"
        existing = exclude.read_text().splitlines() if exclude.exists() else []
        pattern = f"/{self.worktrees_root.name}/"
        if pattern not in existing:
            existing.append(f"# RepoPilot task worktrees (managed by WorktreeManager)")
            existing.append(pattern)
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("\n".join(existing) + "\n")

    # ─── metadata ───────────────────────────────────────────────

    def _meta_path(self, task_id: str) -> Path:
        return self._meta_dir / f"{task_id}.json"

    def _save_meta(self, info: WorktreeInfo) -> None:
        self._meta_path(info.task_id).write_text(json.dumps({
            "task_id": info.task_id,
            "branch": info.branch,
            "path": str(info.path),
            "base_commit": info.base_commit,
            "created_at": info.created_at or datetime.now(timezone.utc).isoformat(),
        }))

    def _load_meta(self, path: Path) -> WorktreeInfo:
        data = json.loads(path.read_text())
        return WorktreeInfo(
            task_id=data["task_id"],
            branch=data["branch"],
            path=Path(data["path"]),
            base_commit=data["base_commit"],
            created_at=data.get("created_at", ""),
        )

    def _delete_meta(self, task_id: str) -> None:
        self._meta_path(task_id).unlink(missing_ok=True)

    # ─── task branch + worktree creation ────────────────────────

    def branch_name(self, task_id: str) -> str:
        """The task branch name for a task id: ``task/<task_id>``. Raises
        WorktreeError for ids that are not valid branch-name segments."""
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            raise WorktreeError(
                f"invalid task id {task_id!r}: use letters/digits/._- "
                "(no spaces, slashes or '..')")
        branch = f"{TASK_BRANCH_PREFIX}{task_id}"
        if not self._git_ok(["check-ref-format", "--branch", branch], self.root):
            raise WorktreeError(
                f"task id {task_id!r} does not form a valid branch name ({branch!r})")
        return branch

    def create(self, task_id: str, *, base_branch: str | None = None,
               require_clean: bool = True) -> WorktreeInfo:
        """Create an independent branch + worktree for one coding task.

        Raises WorktreeError for invalid ids or already-registered tasks,
        WorktreeDirtyError when the main workspace is dirty (unless
        ``require_clean=False``)."""
        branch = self.branch_name(task_id)
        if self._meta_path(task_id).exists():
            raise WorktreeError(f"task {task_id!r} already has a registered worktree")
        path = self.worktrees_root / f"task-{task_id}"
        if path.exists():
            raise WorktreeError(f"worktree path already exists: {path}")
        if self._git_ok(["rev-parse", "--verify", "--quiet", branch], self.root):
            raise WorktreeError(f"branch {branch} already exists")
        if require_clean:
            self._assert_clean(self.root, "main workspace")
        base_ref = base_branch or self._current_branch()
        if not self._git_ok(["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"],
                            self.root):
            raise WorktreeError(f"base ref {base_ref!r} does not resolve to a commit")
        base_commit = self._git(["rev-parse", f"{base_ref}^{{commit}}"], self.root)
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "-b", branch, str(path), base_commit], self.root)
        info = WorktreeInfo(task_id=task_id, branch=branch, path=path,
                            base_commit=base_commit)
        self._save_meta(info)
        return info

    # ─── discovery ──────────────────────────────────────────────

    def get(self, task_id: str) -> WorktreeInfo:
        path = self._meta_path(task_id)
        if not path.exists():
            raise WorktreeError(f"no worktree registered for task {task_id!r}")
        return self._load_meta(path)

    def list(self) -> list[WorktreeInfo]:
        """All registered task worktrees (metadata is the source of truth;
        a task whose directory was removed by hand is still listed so
        cleanup() can finish the job)."""
        return [self._load_meta(p) for p in sorted(self._meta_dir.glob("*.json"))]

    def status(self, task_id: str) -> WorktreeStatus:
        info = self.get(task_id)
        if not info.path.exists():
            raise WorktreeError(f"worktree for task {task_id!r} no longer exists at {info.path}")
        out = self._git(["status", "--porcelain"], info.path)
        changes = [line for line in out.splitlines() if line.strip()]
        return WorktreeStatus(
            branch=info.branch,
            head_commit=self._git(["rev-parse", "HEAD"], info.path),
            clean=not changes,
            changes=changes,
        )

    # ─── diff collection + commit ───────────────────────────────

    def diff(self, task_id: str) -> TaskDiff:
        """The task's independent diff: everything the worktree changed
        since its base commit (committed and uncommitted, tracked and
        untracked) — nothing from any other task or the main workspace."""
        info = self.get(task_id)
        if not info.path.exists():
            raise WorktreeError(f"worktree for task {task_id!r} no longer exists at {info.path}")
        base = info.base_commit
        files = [f for f in
                 self._git(["diff", "--name-only", base], info.path).splitlines() if f.strip()]
        untracked = [f for f in self._git(
            ["ls-files", "--others", "--exclude-standard"], info.path).splitlines() if f.strip()]
        return TaskDiff(
            task_id=task_id,
            branch=info.branch,
            base_commit=base,
            head_commit=self._git(["rev-parse", "HEAD"], info.path),
            files=files,
            untracked=untracked,
            patch=self._git(["diff", base], info.path),
            stat=self._git(["diff", "--stat", base], info.path),
        )

    def commit(self, task_id: str, message: str) -> str:
        """Commit the worktree's changes on the task branch; returns the
        new commit hash. Raises NothingToCommitError when there is nothing
        to record."""
        info = self.get(task_id)
        if not info.path.exists():
            raise WorktreeError(f"worktree for task {task_id!r} no longer exists at {info.path}")
        self._git(["add", "-A"], info.path)
        p = self._run(["commit", "-m", message], info.path)
        if p.returncode != 0:
            if "nothing to commit" in (p.stdout + p.stderr):
                raise NothingToCommitError(f"worktree for task {task_id!r} has nothing to commit")
            raise WorktreeError(
                f"commit failed in task {task_id!r}: {(p.stderr or p.stdout).strip()}")
        return self._git(["rev-parse", "HEAD"], info.path)

    # ─── merge + conflict detection ─────────────────────────────

    def detect_conflicts(self, task_id: str, target_branch: str) -> list[str]:
        """Dry-run: would merging the task branch into ``target_branch``
        conflict? Runs the merge in a throwaway detached worktree and
        returns the conflicted file paths ([] = clean merge). The main
        workspace and the task worktree are never touched."""
        info = self.get(task_id)
        target_commit = self._git(["rev-parse", f"{target_branch}^{{commit}}"], self.root)
        if not self._git_ok(["rev-parse", "--verify", "--quiet", "HEAD"], info.path):
            raise WorktreeError(f"worktree for task {task_id!r} has no HEAD")
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        scratch = self.worktrees_root / f".merge-scratch-{uuid.uuid4().hex[:8]}"
        self._git(["worktree", "add", "--detach", str(scratch), target_commit], self.root)
        try:
            p = self._run(["merge", "--no-edit", info.branch], scratch)
            if p.returncode == 0:
                return []
            files = [f for f in self._git(
                ["diff", "--name-only", "--diff-filter=U"], scratch).splitlines() if f.strip()]
            if files:
                # Restore the scratch tree so it can be removed without force.
                self._git(["merge", "--abort"], scratch)
                return files
            raise WorktreeError(
                f"merge probe for task {task_id!r} failed without conflicts: "
                f"{(p.stderr or p.stdout).strip()}")
        finally:
            # The scratch worktree is ours alone — force removal is safe here
            # and never touches task code.
            self._run(["worktree", "remove", "--force", str(scratch)], self.root)

    def merge(self, task_id: str, target_branch: str, *,
              cwd: str | Path | None = None) -> MergeResult:
        """Merge the task branch into ``target_branch`` (--no-ff, so the
        task stays a recognizable unit).

        ``cwd`` selects the checkout the merge happens in: by default the
        main workspace (which must be checked out on ``target_branch``);
        the DAG executor passes its dedicated integration worktree so the
        user's main workspace is never switched or touched.

        Never force-overwrites: on conflict the merge is aborted and
        WorktreeConflictError is raised, leaving the merge checkout
        byte-identical to its pre-merge state. Requires that checkout to
        be clean and on ``target_branch``."""
        info = self.get(task_id)
        work = Path(cwd).resolve() if cwd is not None else self.root
        self._assert_clean(work, "merge checkout")
        current = self._run(["symbolic-ref", "--short", "HEAD"], work)
        current = current.stdout.strip() if current.returncode == 0 else "HEAD"
        if current != target_branch:
            raise WorktreeError(
                f"merge checkout is on branch {current!r}, not {target_branch!r}; "
                "check out the target branch first")
        p = self._run(["merge", "--no-ff", "--no-edit", info.branch], work)
        if p.returncode == 0:
            return MergeResult(
                task_id=task_id, target_branch=target_branch, merged=True,
                commit=self._git(["rev-parse", "HEAD"], work))
        files = [f for f in self._git(
            ["diff", "--name-only", "--diff-filter=U"], work).splitlines() if f.strip()]
        if not files:
            raise WorktreeError(
                f"merge of {info.branch} into {target_branch} failed without conflicts: "
                f"{(p.stderr or p.stdout).strip()}")
        # Abort restores the pre-merge index and working tree; the clean
        # assertion below guarantees nothing was left behind (禁止暴力覆盖).
        self._git(["merge", "--abort"], work)
        self._assert_clean(work, "merge checkout")
        raise WorktreeConflictError(
            files,
            f"merge of {info.branch} into {target_branch} conflicts in {len(files)} "
            f"file(s); the merge was aborted and nothing was changed")

    # ─── cleanup ────────────────────────────────────────────────

    def cleanup(self, task_id: str, *, force: bool = False) -> None:
        """Remove the task's worktree and branch.

        Never destructive by default: a dirty worktree (uncommitted
        changes would be lost) or a branch with commits not merged into
        the main workspace's HEAD blocks cleanup and raises — passing
        ``force=True`` explicitly discards either. The unmerged-branch
        check runs first so a refused cleanup leaves everything intact."""
        info = self.get(task_id)
        if info.path.exists():
            if not force:
                out = self._git(["status", "--porcelain"], info.path)
                if out.strip():
                    raise WorktreeDirtyError(
                        f"worktree for task {task_id!r} has uncommitted changes; "
                        f"cleanup refused (pass force=True to discard them):\n{out[:800]}")
            if self._git_ok(["rev-parse", "--verify", "--quiet", info.branch], self.root):
                if not force and not self._branch_merged_into_head(info.branch):
                    raise WorktreeError(
                        f"branch {info.branch} has commits not merged into "
                        f"{self._current_branch()!r}; cleanup refused "
                        "(pass force=True to delete it anyway)")
            p = self._run(["worktree", "remove"] +
                          (["--force"] if force else []) + [str(info.path)], self.root)
            if p.returncode != 0:
                raise WorktreeError(
                    f"failed to remove worktree for task {task_id!r}: {p.stderr.strip()}")
        if self._git_ok(["rev-parse", "--verify", "--quiet", info.branch], self.root):
            p = self._run(["branch", "-D" if force else "-d", info.branch], self.root)
            if p.returncode != 0:
                raise WorktreeError(
                    f"failed to delete branch {info.branch}: {p.stderr.strip()}")
        self._delete_meta(task_id)

    def cleanup_all(self, *, force: bool = False) -> list[str]:
        """Clean up every registered task; returns the removed task ids.
        Stops at the first refusal (nothing after it is touched)."""
        removed = []
        for info in self.list():
            self.cleanup(info.task_id, force=force)
            removed.append(info.task_id)
        return removed

    def _branch_merged_into_head(self, branch: str) -> bool:
        # `git branch --merged` prefixes the current branch with '*' and
        # branches checked out in linked worktrees with '+'.
        listed = self._git(["branch", "--merged"], self.root)
        return any(line.strip().lstrip("*+ ") == branch for line in listed.splitlines())
