"""Phase 11 — Task DAG execution engine.

DagRunner executes the planning TaskDAG for real: one integration
worktree, one parallel worktree per READY task, one ACL-bound role agent
per task, per-task verification + bounded self-repair, and honest merge
conflict handling (never force-overwrite). This is the engine
``repopilot run`` now drives instead of the fixed five-role pipeline.
"""

from .runner import DagRunReport, DagRunner, TaskOutcome

__all__ = ["DagRunner", "DagRunReport", "TaskOutcome"]
