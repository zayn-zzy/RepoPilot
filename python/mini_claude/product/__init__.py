"""Phase 10 — Productization.

The repopilot CLI (init/index/ask/plan/run/graph/benchmark), the GitHub
issue→PR flow (issue → requirement → run → patch → commit → PR body →
pull request), and the observability RunLog that records every agent run
(doc §24) into append-only JSONL — the same data the benchmark consumes."""

from .github import (
    PR_SECTIONS,
    build_pr_body,
    create_pull_request,
    fetch_issue,
    issue_to_requirement,
)  # noqa: F401
from .orchestrator import (  # noqa: F401
    RunReport,
    run_dag_requirement,
    run_requirement,
)
from .runlog import RunLogger, RunRecord, RunRecorder  # noqa: F401

__all__ = [
    "RunRecord", "RunRecorder", "RunLogger",
    "issue_to_requirement", "build_pr_body", "PR_SECTIONS",
    "fetch_issue", "create_pull_request",
    "run_requirement", "run_dag_requirement", "RunReport",
]
