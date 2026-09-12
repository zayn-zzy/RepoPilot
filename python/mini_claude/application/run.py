"""RunService — the `repopilot run` / `issue` use case as a callable
service (Web Phase 1).

Plan → DAG execution → report → optional GitHub flow. No prints; the
CLI formats RunResult for the terminal, the Web layer will persist and
stream it (WP5). `after_build`/`logger` are the test seams (scripted
LLMs, the same contract as the orchestrator tests)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import ApplicationError
from .repository import _repopilot_dir


@dataclass
class RunResult:
    task_id: str
    report: Any                  # DagRunReport
    github: Any | None = None    # GithubResult or None (no GitHub opts)
    pr_file: Path | None = None
    success: bool = False


class RunService:
    async def run(self, path: str | Path, requirement, *,
                  plan: Any = None,
                  task_id: str | None = None,
                  model: str = "deepseek-v4-pro[1m]",
                  api_key: str | None = None,
                  base_url: str | None = None,
                  jobs: int = 1,
                  sandbox: str = "auto",
                  commit: bool = True,
                  push: bool = False,
                  pr: bool = False,
                  merge: bool = False,
                  cleanup: bool = False,
                  squash: bool = False,
                  after_build: Callable[[Any], None] | None = None,
                  logger: Any | None = None,
                  ) -> RunResult:
        """Run one requirement through its Task DAG and the optional
        GitHub flow. Everything here is the same code path the CLI
        uses (product.orchestrator + product.github_flow)."""
        from ..product.orchestrator import run_dag_requirement
        root = Path(path).resolve()
        if not api_key:
            raise ApplicationError(
                "API_KEY_REQUIRED",
                "a run needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN)")
        run_id = task_id or f"T{int(time.time()) % 100000}"
        report = await run_dag_requirement(
            root, requirement, plan=plan, task_id=run_id,
            model=model, api_key=api_key, anthropic_base_url=base_url,
            jobs=jobs, sandbox=sandbox, commit=commit,
            after_build=after_build, logger=logger,
        )
        result = RunResult(task_id=run_id, report=report,
                           success=bool(report.success))
        return await self.finish(root, result,
                                 push=push, pr=pr, merge=merge,
                                 cleanup=cleanup, squash=squash)

    async def finish(self, root: Path, result: RunResult, *,
                     push: bool = False, pr: bool = False,
                     merge: bool = False, cleanup: bool = False,
                     squash: bool = False) -> RunResult:
        """The Phase 17 post-run GitHub flow + PR body write. Without
        GitHub opts this only writes the PR body (CLI-compatible)."""
        report = result.report
        if report.pr is not None:
            result.pr_file = _repopilot_dir(root) / f"pr-{result.task_id}.md"
            result.pr_file.write_text(report.pr.body)
        if push or pr or merge or cleanup:
            from ..product.github_flow import run_github_flow
            from ..worktree import WorktreeManager
            result.github = run_github_flow(
                root, report, push=push, pr=pr, merge=merge,
                cleanup=cleanup, merge_method="squash" if squash else "merge",
                manager=WorktreeManager(root))
        return result
