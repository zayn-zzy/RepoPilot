"""run_requirement — the full RepoPilot composition.

    Requirement
        → WorktreeManager.create (task branch + worktree)
        → TeamRunner (five roles, bound to the worktree)
        → VerificationPipeline (the repo's own tests, for real)
        → SelfRepairEngine (bounded, only when verification fails)
        → WorktreeManager.commit + diff collection
        → PR description (doc's six sections)
        → RunLogger record (doc §24)

This is what `repopilot run` and the GitHub issue flow both call. The
main workspace is never touched: the team writes inside the worktree;
conflicts never overwrite (Phase 6); repair is bounded (Phase 7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..agents import TeamConfig, TeamRunner
from ..planning import Requirement, RequirementParser
from ..verify import SelfRepairEngine, VerificationPipeline
from ..worktree import WorktreeInfo, WorktreeManager
from .github import PrInfo, build_pr_body
from .runlog import RunLogger, RunRecord, RunRecorder


@dataclass
class RunReport:
    """Everything one repopilot run produced."""

    task_id: str
    requirement: Requirement
    worktree: WorktreeInfo | None = None
    team_result: Any | None = None
    verification: dict | None = None
    repair: dict | None = None
    diff: Any | None = None
    commit: str = ""
    pr: PrInfo | None = None
    runlog_records: list[RunRecord] = field(default_factory=list)
    success: bool = False
    note: str = ""

    def summarize(self) -> str:
        lines = [f"run {self.task_id}: {'SUCCESS' if self.success else 'FAILED'}"]
        if self.worktree is not None:
            lines.append(f"  worktree: {self.worktree.path} ({self.worktree.branch})")
        if self.team_result is not None:
            lines.append(f"  roles run: "
                         f"{[o.role for o in self.team_result.outcomes]}")
            lines.append(f"  reviewer approved: {self.team_result.approved}")
        if self.verification:
            lines.append(
                f"  verification: {'PASS' if self.verification['passed'] else 'FAIL'} "
                f"{self.verification.get('tests_passed', 0)}/"
                f"{self.verification.get('tests_total', 0)} tests")
        if self.repair and self.repair.get("attempts"):
            lines.append(f"  self-repair: {self.repair['attempts']} attempt(s), "
                         f"success={self.repair.get('success')}")
        if self.diff is not None:
            lines.append(f"  files changed: {self.diff.all_files}")
        if self.commit:
            lines.append(f"  commit: {self.commit[:12]}")
        if self.pr is not None:
            lines.append(f"  PR title: {self.pr.title}")
        if self.note:
            lines.append(f"  note: {self.note}")
        return "\n".join(lines)


async def run_requirement(root: str | Path, requirement: str | Requirement, *,
                          task_id: str = "TASK",
                          model: str = "mock-model",
                          api_key: str | None = None,
                          anthropic_base_url: str | None = None,
                          permission_mode: str = "acceptEdits",
                          team_max_cost_usd: float = 1.5,
                          team_max_turns: int = 10,
                          repair_max_cost_usd: float = 1.0,
                          max_repair_attempts: int = 3,
                          after_build: Callable[[Any], None] | None = None,
                          logger: RunLogger | None = None,
                          base_branch: str | None = None,
                          commit: bool = True,
                          ) -> RunReport:
    """Run one requirement end-to-end. ``after_build`` receives every
    freshly built role runtime (tests inject scripted LLMs here)."""
    root = Path(root).resolve()
    if isinstance(requirement, str):
        requirement = RequirementParser().parse(requirement)
    report = RunReport(task_id=task_id, requirement=requirement)
    logger = logger or RunLogger(root / ".repopilot" / "runs.jsonl")
    _ensure_local_exclude(root, "/.repopilot/")

    try:
        manager = WorktreeManager(root)
        info = manager.create(task_id, base_branch=base_branch)
        report.worktree = info
    except Exception as e:
        report.note = f"worktree creation failed: {e}"
        return report

    recorders: dict[str, RunRecorder] = {}

    def after_build_all(runtime):
        rec = RunRecorder(task=requirement.title, agent=runtime.config.role)
        rec.attach(runtime)
        recorders[runtime.config.role] = rec
        if after_build is not None:
            after_build(runtime)

    previous_cwd = os.getcwd()
    try:
        from ..repo import RepositoryIndex
        index = RepositoryIndex(info.path)
        index.build()
        team = TeamRunner(TeamConfig(
            model=model, index=index, api_key=api_key,
            anthropic_base_url=anthropic_base_url,
            permission_mode=permission_mode,
            max_cost_usd=team_max_cost_usd, max_turns=team_max_turns,
            worktree=info,
        ), after_build=after_build_all)
        team_result = await team.run(requirement)
        report.team_result = team_result
    finally:
        os.chdir(previous_cwd)

    # Verification on the worktree (real test runs, Phase 7) — graded
    # with the Phase 9 grader.
    from ..evaluation import grade
    resolved, passed, total, summary = grade(info.path)
    verification = {"passed": resolved, "tests_passed": passed,
                    "tests_total": total, "summary": summary[:500]}
    report.verification = verification

    repair = {"attempts": 0, "success": None}
    if not resolved:
        failure = VerificationPipeline(info.path).run().first_failure
        if failure is not None:
            engine = SelfRepairEngine(
                info.path, model=model, api_key=api_key,
                anthropic_base_url=anthropic_base_url,
                permission_mode=permission_mode,
                max_cost_usd=repair_max_cost_usd,
                max_repair_attempts=max_repair_attempts,
            )
            repair_result = await engine.repair(failure)
            repair = {"attempts": len(repair_result.attempts),
                      "success": repair_result.fixed,
                      "total_cost_usd": repair_result.total_cost_usd}
            resolved, passed, total, summary = grade(info.path)
            verification = {"passed": resolved, "tests_passed": passed,
                            "tests_total": total, "summary": summary[:500]}
            report.verification = verification
    report.repair = repair

    # Patch + commit + PR description. Verification artifacts (pycache,
    # pytest caches) are not source changes — purge them from the
    # worktree before collecting the diff (they must never enter the
    # task patch or the commit).
    for cache in ("__pycache__", ".pytest_cache"):
        for p in info.path.rglob(cache):
            if p.is_dir():
                import shutil
                shutil.rmtree(p, ignore_errors=True)
    diff = manager.diff(task_id)
    report.diff = diff
    if commit and diff.all_files:
        try:
            report.commit = manager.commit(task_id, f"repopilot: {requirement.title}")
        except Exception as e:
            report.note = f"commit failed: {e}"
    report.pr = PrInfo(
        title=f"repopilot: {requirement.title}",
        body=build_pr_body(requirement=requirement, diff=diff,
                           team_result=team_result,
                           verification=verification, repair=repair,
                           base_branch=base_branch or "main"),
        head_branch=info.branch,
        base_branch=base_branch or "main",
    )

    # Observability records (doc §24): one per role + the final result.
    final = {"success": bool(verification["passed"]),
             "tests_total": verification["tests_total"],
             "tests_passed": verification["tests_passed"],
             "commit": report.commit}
    for role, rec in recorders.items():
        rec.finish(_last_run(rec, team_result), verification=verification,
                   repair=repair, final_result=final)
        logger.record(rec.record)
        report.runlog_records.append(rec.record)
    report.success = bool(verification["passed"])
    return report


async def run_dag_requirement(root: str | Path, requirement: str | Requirement, *,
                              plan: Any,                     # planning.TaskDAG
                              task_id: str = "TASK",
                              model: str = "mock-model",
                              api_key: str | None = None,
                              anthropic_base_url: str | None = None,
                              permission_mode: str = "acceptEdits",
                              task_max_cost_usd: float = 0.75,
                              task_max_turns: int = 12,
                              repair_max_cost_usd: float = 0.5,
                              max_repair_attempts: int = 3,
                              jobs: int = 1,
                              after_build: Callable[[Any], None] | None = None,
                              logger: RunLogger | None = None,
                              base_branch: str | None = None,
                              commit: bool = True,
                              ) -> "DagRunReport":
    """Phase 11 composition — run one requirement through its TaskDAG:

        plan → DagRunner (parallel task worktrees, per-task agent +
        verification + bounded repair, honest merges) → final verification
        → diff → PR description.

    The main workspace is never touched: every task works in its own
    worktree and merges land in the dedicated integration worktree.
    ``after_build`` receives every freshly built task runtime (tests
    inject scripted LLM clients here)."""
    from ..execution import DagRunner
    root = Path(root).resolve()
    if isinstance(requirement, str):
        requirement = RequirementParser().parse(requirement)
    logger = logger or RunLogger(root / ".repopilot" / "runs.jsonl")
    _ensure_local_exclude(root, "/.repopilot/")

    runner = DagRunner(
        root, plan, task_id=task_id, model=model, api_key=api_key,
        anthropic_base_url=anthropic_base_url,
        permission_mode=permission_mode,
        task_max_cost_usd=task_max_cost_usd, task_max_turns=task_max_turns,
        repair_max_cost_usd=repair_max_cost_usd,
        max_repair_attempts=max_repair_attempts, jobs=jobs,
        base_branch=base_branch, commit=commit, after_build=after_build,
        recorder_factory=lambda title, agent: RunRecorder(task=title,
                                                          agent=agent),
        logger=logger,
    )
    report = await runner.run(requirement)
    if report.worktree is not None and report.diff is not None:
        repair = {"attempts": sum((o.repair or {}).get("attempts", 0)
                                  for o in report.outcomes),
                  "success": all((o.repair or {}).get("success")
                                 for o in report.outcomes
                                 if (o.repair or {}).get("attempts"))}
        report.pr = PrInfo(
            title=f"repopilot: {requirement.title}",
            body=build_pr_body(requirement=requirement, diff=report.diff,
                               team_result=None,
                               verification=report.final_verification,
                               repair=repair,
                               base_branch=base_branch or "main"),
            head_branch=report.worktree.branch,
            base_branch=base_branch or "main",
        )
    return report


def _last_run(recorder: RunRecorder, team_result) -> Any:
    """The RunResult for a recorder's role (RunRecorder doesn't keep it —
    look it up from the team outcomes by role)."""
    for outcome in team_result.outcomes:
        if outcome.role == recorder.record.agent:
            return outcome.run
    return None


def _ensure_local_exclude(root: Path, pattern: str) -> None:
    """Keep the main workspace clean: add the pattern to .git/info/exclude
    (local-only, never touches tracked files) — the same discipline as the
    Phase 6 worktrees/ exclusion."""
    import subprocess
    git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=str(root),
        capture_output=True, text=True).stdout.strip()
    if not git_dir:
        return
    exclude = Path(git_dir) / "info" / "exclude"
    existing = exclude.read_text().splitlines() if exclude.exists() else []
    if pattern not in existing:
        existing.append(f"# RepoPilot local state (managed by repopilot)")
        existing.append(pattern)
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(existing) + "\n")
