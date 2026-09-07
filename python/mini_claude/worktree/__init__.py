"""Phase 6 — Git Worktree Isolation.

One coding task = one branch + one worktree + one working directory + one
independent git diff. Parallel tasks can never pollute each other's
filesystem state, and merges never force-overwrite on conflicts."""

from .manager import (  # noqa: F401
    NothingToCommitError,
    MergeResult,
    TaskDiff,
    WorktreeConflictError,
    WorktreeDirtyError,
    WorktreeError,
    WorktreeInfo,
    WorktreeManager,
    WorktreeStatus,
)

__all__ = [
    "WorktreeManager",
    "WorktreeInfo",
    "WorktreeStatus",
    "TaskDiff",
    "MergeResult",
    "WorktreeError",
    "WorktreeDirtyError",
    "WorktreeConflictError",
    "NothingToCommitError",
]
