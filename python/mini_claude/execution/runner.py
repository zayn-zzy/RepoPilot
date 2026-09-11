"""TaskDAG execution engine — the missing link between planning/ and
``repopilot run``.

    Requirement → Planner (TaskDAG) → DagRunner
        → one integration worktree   (branch: task/<run_id>)
        → per-task worktrees         (branch: task/<run_id>-<node>), one per READY task
        → one role agent per task (ACL-bound, worktree-bound)
        → per-task verification + bounded self-repair
        → commit → merge into the integration branch (never force-overwrite)
        → final verification + diff on the integration worktree

The planning Scheduler drives the loop: only tasks whose dependencies
all SUCCEEDED are ever handed out; failures cascade to dependents
(BLOCKED). Independent tasks run in parallel threads, each thread
binding its own worktree, agent and thread-local work root (tools.py)
— parallel file tools can never touch another task's checkout, and the
user's main workspace is never switched or written to. A merge conflict
marks the task failed and aborts the merge: nothing is ever
force-overwritten (禁止冲突时暴力覆盖代码).
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..evaluation import grade
from ..planning import Requirement, RequirementParser, TaskDAG, TaskNode
from ..planning.dag import Scheduler
from ..sandbox import SandboxedCommandRunner, set_sandbox
from ..tools import set_work_root
from ..verify import SelfRepairEngine, VerificationPipeline
from ..worktree import (
    TaskDiff,
    WorktreeConflictError,
    WorktreeInfo,
    WorktreeManager,
)
from ..worktree.manager import TASK_ID_RE

# after_build(runtime, task) — invoked with every freshly built agent
# runtime (tests inject scripted LLM clients here, per DAG task).
AfterBuild = Callable[[Any, TaskNode], None]
# recorder_factory(task_title, agent_role) → recorder with
# .attach(runtime) / .finish(run, *, verification, repair, final_result)
# / .record — supplied by the product layer (RunRecorder) so this module
# stays independent of product/ (avoids the import cycle).
RecorderFactory = Callable[[str, str], Any]


@dataclass
class TaskOutcome:
    """What one DAG task produced (or why it did not)."""

    task_id: str                  # the DAG node id (e.g. T001)
    worktree_task_id: str         # the worktree/branch task id (run_id-T001)
    role: str
    status: str = "pending"       # succeeded | failed | conflict | blocked | pending
    worktree: WorktreeInfo | None = None
    verification: dict | None = None
    repair: dict | None = None
    diff: TaskDiff | None = None
    commit: str = ""
    cost_usd: float = 0.0
    error: str = ""


@dataclass
class DagRunReport:
    """Everything one DAG run produced (integration level)."""

    task_id: str
    requirement: Requirement
    plan: TaskDAG
    outcomes: list[TaskOutcome] = field(default_factory=list)
    worktree: WorktreeInfo | None = None      # the integration worktree
    final_verification: dict | None = None
    diff: TaskDiff | None = None
    commit: str = ""
    pr: Any | None = None                     # PrInfo, attached by the product layer
    runlog_records: list = field(default_factory=list)
    sandbox: str = ""                         # honest record of where commands ran
    success: bool = False
    note: str = ""

    def summarize(self) -> str:
        counts = ", ".join(
            f"{s}={sum(1 for o in self.outcomes if o.status == s)}"
            for s in sorted({o.status for o in self.outcomes}))
        lines = [f"run {self.task_id}: {'SUCCESS' if self.success else 'FAILED'} "
                 f"({len(self.outcomes)} task(s): {counts})"]
        for o in self.outcomes:
            line = f"  - {o.task_id} [{o.role}] {o.status}"
            if o.commit:
                line += f" commit={o.commit[:12]}"
            if o.verification:
                line += (f" tests {o.verification.get('tests_passed', 0)}/"
                         f"{o.verification.get('tests_total', 0)}")
            if o.repair and o.repair.get("attempts"):
                line += f" repair={o.repair['attempts']}x"
            if o.cost_usd:
                line += f" ${o.cost_usd:.4f}"
            if o.error:
                line += f" — {o.error[:140]}"
            lines.append(line)
        if self.worktree is not None:
            lines.append(f"  worktree: {self.worktree.path} ({self.worktree.branch})")
        if self.sandbox:
            lines.append(f"  sandbox: {self.sandbox}")
        if self.final_verification:
            lines.append(
                f"  final verification: "
                f"{'PASS' if self.final_verification['passed'] else 'FAIL'} "
                f"{self.final_verification.get('tests_passed', 0)}/"
                f"{self.final_verification.get('tests_total', 0)} tests")
        if self.diff is not None:
            lines.append(f"  files changed: {self.diff.all_files}")
        if self.pr is not None:
            lines.append(f"  PR title: {self.pr.title}")
        if self.note:
            lines.append(f"  note: {self.note}")
        return "\n".join(lines)


class DagRunner:
    """Executes a validated TaskDAG over one repository.

    Concurrency model: the scheduler thread hands READY tasks to a
    ThreadPoolExecutor of ``jobs`` workers; each task runs in its own
    thread with its own event loop for the agent, its own worktree, and
    its own thread-local work root. Merges into the integration branch
    are serialized under one lock (they all advance the same branch).

    The runner mutates ``plan``'s task statuses as it runs (the
    Scheduler contract) — build a fresh plan per run.
    """

    def __init__(self, root: str | Path, plan: TaskDAG, *,
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
                 base_branch: str | None = None,
                 commit: bool = True,
                 sandbox: str = "auto",        # off | auto | on (Phase 12)
                 sandbox_builder: Callable[[Path], Any] | None = None,
                 after_build: AfterBuild | None = None,
                 manager: WorktreeManager | None = None,
                 recorder_factory: RecorderFactory | None = None,
                 logger: Any | None = None,
                 ):
        """``sandbox`` is the Phase 12 command-execution mode for every
        task's shell/tests/verification: "off" = host (pre-wiring),
        "auto" = docker when available else host with an honest note,
        "on" = docker required (unavailable docker fails the run up
        front). ``sandbox_builder(worktree_path)`` injects the
        per-task SandboxedCommandRunner (tests)."""
        self.root = Path(root).resolve()
        self.plan = plan
        self.task_id = task_id
        self.model = model
        self.api_key = api_key
        self.anthropic_base_url = anthropic_base_url
        self.permission_mode = permission_mode
        self.task_max_cost_usd = task_max_cost_usd
        self.task_max_turns = task_max_turns
        self.repair_max_cost_usd = repair_max_cost_usd
        self.max_repair_attempts = max_repair_attempts
        self.jobs = jobs
        self.base_branch = base_branch
        self.commit = commit
        self.sandbox = sandbox
        self._sandbox_builder = sandbox_builder
        self._after_build = after_build
        self.manager = manager
        self._recorder_factory = recorder_factory
        self.logger = logger
        self._merge_lock = threading.Lock()
        self._sandbox_runners: list = []      # per-task runners (stats) + final

    # ─── entry point ──────────────────────────────────────────

    async def run(self, requirement: str | Requirement) -> DagRunReport:
        if isinstance(requirement, str):
            requirement = RequirementParser().parse(requirement)
        report = DagRunReport(task_id=self.task_id, requirement=requirement,
                              plan=self.plan)
        if self.jobs < 1:
            report.note = f"jobs must be >= 1, got {self.jobs}"
            return report
        if self.sandbox not in ("off", "auto", "on"):
            report.note = f"unknown sandbox mode {self.sandbox!r} (off|auto|on)"
            return report
        # "on" demands docker — probe before any worktree is created so
        # the refusal is up front and unambiguous (no silent host fallback).
        if self.sandbox == "on" and self._sandbox_builder is None:
            probe = SandboxedCommandRunner(self.root, mode="on")
            if not probe.available:
                report.note = (f"sandbox mode 'on' but docker is unavailable "
                               f"({probe._probe_reason or 'daemon unreachable'}) "
                               f"— no task ran; use --sandbox auto to fall back "
                               f"to host execution, or install/start docker")
                return report
        validation = self.plan.validate()
        if not validation.valid:
            report.note = "invalid plan: " + "; ".join(validation.errors)
            return report
        bad_ids = [t.id for t in self.plan.tasks.values()
                   if not TASK_ID_RE.match(f"{self.task_id}-{t.id}")]
        if bad_ids:
            report.note = ("task id(s) cannot form worktree branch names: "
                           + ", ".join(bad_ids))
            return report

        self.manager = self.manager or WorktreeManager(self.root)
        try:
            integration = self.manager.create(self.task_id,
                                              base_branch=self.base_branch,
                                              require_clean=False)
        except Exception as e:
            report.note = f"integration worktree creation failed: {e}"
            return report
        report.worktree = integration

        try:
            scheduler = Scheduler(self.plan, max_attempts=0)
        except Exception as e:
            report.note = f"cannot schedule the plan: {e}"
            return report
        try:
            await asyncio.to_thread(self._scheduler_loop, scheduler, integration,
                                    requirement, report)
        except Exception as e:
            report.note = f"scheduler loop failed: {e}"

        self._synthesize_unrun_outcomes(report)
        self._finalize(report, integration)
        return report

    # ─── scheduler loop (one thread) ──────────────────────────

    def _scheduler_loop(self, scheduler: Scheduler, integration: WorktreeInfo,
                        requirement: Requirement, report: DagRunReport) -> None:
        """Claim READY tasks (up to ``jobs`` at once), wait for one to
        finish, report its outcome, repeat until nothing can run."""
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            pending: dict = {}
            while True:
                scheduler.dag.refresh_ready()
                while len(pending) < self.jobs:
                    task = scheduler.next_ready()
                    if task is None:
                        break
                    pending[pool.submit(self._execute_task, task, integration,
                                        requirement, report)] = task
                if not pending:
                    break  # nothing running, nothing ready — the rest is BLOCKED
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    task = pending.pop(fut)
                    outcome = fut.result()
                    report.outcomes.append(outcome)
                    scheduler.complete(task.id, outcome.status == "succeeded",
                                       result=self._outcome_result(outcome))

    @staticmethod
    def _outcome_result(outcome: TaskOutcome) -> str:
        if outcome.error:
            return f"{outcome.status}: {outcome.error}"
        if outcome.commit:
            return f"{outcome.status}: commit {outcome.commit[:12]}"
        return f"{outcome.status}: no changes"

    # ─── one task (one worker thread) ─────────────────────────

    def _execute_task(self, task: TaskNode, integration: WorktreeInfo,
                      requirement: Requirement,
                      report: DagRunReport) -> TaskOutcome:
        wt_id = f"{self.task_id}-{task.id}"
        outcome = TaskOutcome(task_id=task.id, worktree_task_id=wt_id,
                              role=task.agent_role)
        try:
            info = self.manager.create(wt_id, base_branch=integration.branch,
                                       require_clean=False)
            outcome.worktree = info
        except Exception as e:
            outcome.status = "failed"
            outcome.error = f"worktree creation failed: {e}"
            return outcome

        # This thread's file/shell tools resolve against the task worktree
        # (thread-local root) — parallel task threads can never touch each
        # other's checkout, and the process cwd becomes irrelevant.
        set_work_root(str(info.path))
        sandbox = self._build_sandbox(info.path)
        if sandbox is not None:
            if sandbox.mode == "on" and not sandbox.available:
                outcome.status = "failed"
                outcome.error = ("sandbox mode 'on' but docker is unavailable"
                                 f" ({sandbox._probe_reason or 'daemon unreachable'})")
                set_work_root(None)
                return outcome
            set_sandbox(sandbox)
            self._sandbox_runners.append(sandbox)
        try:
            self._run_task_body(task, info, integration, requirement, outcome,
                                report, sandbox)
        finally:
            set_sandbox(None)
            set_work_root(None)
        return outcome

    def _run_task_body(self, task: TaskNode, info: WorktreeInfo,
                       integration: WorktreeInfo, requirement: Requirement,
                       outcome: TaskOutcome, report: DagRunReport,
                       sandbox=None) -> None:
        run_result = None
        recorder = None
        try:
            run_result, cost, recorder = asyncio.run(
                self._run_agent(task, info, requirement))
            outcome.cost_usd += cost
        except Exception as e:
            outcome.status = "failed"
            outcome.error = f"agent run failed: {e}"
            self._record(recorder, run_result, outcome, report)
            return

        outcome.verification, outcome.repair = self._verify_and_repair(
            task, info, sandbox)
        outcome.cost_usd += outcome.repair.get("total_cost_usd", 0.0)

        _purge_caches(info.path)
        try:
            outcome.diff = self.manager.diff(outcome.worktree_task_id)
        except Exception as e:
            outcome.status = "failed"
            outcome.error = f"diff collection failed: {e}"
            self._record(recorder, run_result, outcome, report)
            return

        if self.commit and outcome.diff.all_files:
            try:
                outcome.commit = self.manager.commit(
                    outcome.worktree_task_id,
                    f"repopilot[{self.task_id}]: {task.id} {task.title}")
            except Exception as e:
                outcome.status = "failed"
                outcome.error = f"commit failed: {e}"
                self._record(recorder, run_result, outcome, report)
                return

        if not outcome.verification["passed"]:
            # A broken task is committed (preserved for inspection) but
            # never merged — broken code must not enter the result.
            outcome.status = "failed"
            outcome.error = ("task verification failed"
                             + (f" after {outcome.repair['attempts']} repair "
                                f"attempt(s)" if outcome.repair["attempts"] else ""))
            self._record(recorder, run_result, outcome, report)
            return

        if outcome.commit:
            try:
                with self._merge_lock:
                    if self._branch_ahead(outcome.worktree_task_id,
                                          integration.branch):
                        self.manager.merge(outcome.worktree_task_id,
                                           integration.branch,
                                           cwd=integration.path)
            except WorktreeConflictError as e:
                # 禁止冲突时暴力覆盖代码: the merge was aborted and the
                # integration worktree is byte-identical to before.
                outcome.status = "conflict"
                outcome.error = str(e)
                self._record(recorder, run_result, outcome, report)
                return
            except Exception as e:
                outcome.status = "failed"
                outcome.error = f"merge failed: {e}"
                self._record(recorder, run_result, outcome, report)
                return
        outcome.status = "succeeded"
        self._record(recorder, run_result, outcome, report)

    async def _run_agent(self, task: TaskNode, info: WorktreeInfo,
                         requirement: Requirement):
        """One role agent for one task: ACL-bound, worktree-bound, its own
        index and context. Returns (run_result, cost_usd, recorder)."""
        from ..agents.artifact import ArtifactMailbox
        from ..agents.roles import build_role_registry, build_role_runtime
        from ..repo import RepositoryIndex

        index = RepositoryIndex(info.path)
        index.build()
        mailbox = ArtifactMailbox()
        registry = build_role_registry(index, mailbox, git_root=info.path)
        budget = task.budget
        runtime = build_role_runtime(
            task.agent_role, index, mailbox,
            model=self.model, api_key=self.api_key,
            registry=registry,
            anthropic_base_url=self.anthropic_base_url,
            permission_mode=self.permission_mode,
            max_cost_usd=budget.max_cost_usd or self.task_max_cost_usd,
            max_turns=budget.max_turns or self.task_max_turns,
        )
        if self._after_build is not None:
            self._after_build(runtime, task)
        recorder = None
        if self._recorder_factory is not None:
            recorder = self._recorder_factory(task.title, task.agent_role)
            recorder.attach(runtime)
        run = await runtime.run(self._task_prompt(task, requirement))
        cost = run.trace.metrics().get("cost_usd", 0.0)
        await runtime.close()
        return run, cost, recorder

    @staticmethod
    def _task_prompt(task: TaskNode, requirement: Requirement) -> str:
        parts = [
            f"Requirement (kind={requirement.kind.value}): {requirement.title}",
            requirement.description,
            "",
            f"Your task ({task.id}): {task.title}",
            task.description,
        ]
        if task.files:
            parts.append(f"Files this task concerns: {', '.join(task.files)}")
        parts += [
            "",
            "Work inside this git worktree: make the minimal focused change, "
            "verify it, then call publish_artifact with your structured result.",
        ]
        return "\n".join(parts)

    def _verify_and_repair(self, task: TaskNode, info: WorktreeInfo,
                           sandbox=None) -> tuple[dict, dict]:
        """The repo's own tests for real (grade), then the bounded
        self-repair loop only when verification fails (Phase 7). Every
        command goes through the task's sandbox runner (Phase 12)."""
        verification = self._grade_dict(info.path, sandbox)
        repair = {"attempts": 0, "success": None, "total_cost_usd": 0.0}
        if verification["passed"]:
            return verification, repair
        failure = VerificationPipeline(info.path, sandbox=sandbox).run().first_failure
        if failure is None:
            return verification, repair
        engine = SelfRepairEngine(
            info.path, model=self.model, api_key=self.api_key,
            anthropic_base_url=self.anthropic_base_url,
            permission_mode=self.permission_mode,
            max_cost_usd=self.repair_max_cost_usd,
            max_repair_attempts=self.max_repair_attempts,
            after_build=(lambda rt: self._after_build(rt, task))
                        if self._after_build is not None else None,
            sandbox=sandbox,
        )
        try:
            result = asyncio.run(engine.repair(failure))
        except Exception as e:
            repair["error"] = f"repair loop failed: {e}"
            return verification, repair
        repair = {"attempts": len(result.attempts), "success": result.fixed,
                  "total_cost_usd": result.total_cost_usd}
        verification = self._grade_dict(info.path, sandbox)
        return verification, repair

    @staticmethod
    def _grade_dict(path: Path, sandbox=None) -> dict:
        resolved, passed, total, summary = grade(path, sandbox=sandbox)
        return {"passed": resolved, "tests_passed": passed,
                "tests_total": total, "summary": summary[:500]}

    def _branch_ahead(self, task_worktree_id: str, target_branch: str) -> bool:
        """Does the task branch carry commits the integration branch does
        not have yet? (git merge refuses empty merges.)"""
        branch = self.manager.branch_name(task_worktree_id)
        count = self.manager._git(
            ["rev-list", "--count", f"{target_branch}..{branch}"],
            self.manager.root)
        return int(count or "0") > 0

    # ─── recording + finalization ─────────────────────────────

    def _record(self, recorder, run_result, outcome: TaskOutcome,
                report: DagRunReport) -> None:
        if recorder is None or run_result is None:
            return
        final = {"success": outcome.status == "succeeded",
                 "tests_total": (outcome.verification or {}).get("tests_total", 0),
                 "tests_passed": (outcome.verification or {}).get("tests_passed", 0),
                 "commit": outcome.commit}
        recorder.finish(run_result, verification=outcome.verification,
                        repair=outcome.repair, final_result=final)
        if self.logger is not None:
            self.logger.record(recorder.record)
        report.runlog_records.append(recorder.record)

    def _synthesize_unrun_outcomes(self, report: DagRunReport) -> None:
        """Tasks the scheduler never ran (BLOCKED by a failed dependency)
        still get an outcome row so the report shows the whole plan."""
        seen = {o.task_id for o in report.outcomes}
        for node in report.plan.tasks.values():
            if node.id in seen:
                continue
            status = node.status.value
            outcome = TaskOutcome(
                task_id=node.id,
                worktree_task_id=f"{self.task_id}-{node.id}",
                role=node.agent_role,
                status=status if status in ("blocked", "skipped") else "pending",
            )
            if status == "blocked":
                failed_deps = [d for d in node.dependencies
                               if report.plan.tasks[d].status.value in
                               ("failed", "blocked")]
                outcome.error = ("blocked: dependency "
                                 + ", ".join(failed_deps) + " did not succeed")
            report.outcomes.append(outcome)
        order = report.plan.topological_order()
        report.outcomes.sort(key=lambda o: order.index(o.task_id))

    def _build_sandbox(self, path: Path):
        """The per-task SandboxedCommandRunner (Phase 12). The workspace
        mount is the task worktree — disposable and cache-purged, so a
        writable mount is safe; the container still gets network=none,
        filtered env, non-root, cpu/memory/pids limits."""
        if self._sandbox_builder is not None:
            return self._sandbox_builder(path)
        if self.sandbox == "off":
            return None
        from ..sandbox import SandboxPolicy
        policy = SandboxPolicy(workspace=Path(path),
                               workspace_writable=True)
        return SandboxedCommandRunner(path, policy=policy, mode=self.sandbox)

    def _finalize(self, report: DagRunReport, integration: WorktreeInfo) -> None:
        final_sandbox = None
        if self.sandbox != "off":
            final_sandbox = self._build_sandbox(integration.path)
            if final_sandbox is not None:
                self._sandbox_runners.append(final_sandbox)
        try:
            report.final_verification = self._grade_dict(integration.path,
                                                         final_sandbox)
        except Exception as e:
            report.note = f"final verification failed: {e}"
        # The honest aggregate: where did commands actually run?
        labels = list(dict.fromkeys(r.label for r in self._sandbox_runners))
        if labels:
            totals = {"docker": 0, "host": 0, "blocked": 0}
            for r in self._sandbox_runners:
                for k in totals:
                    totals[k] += r.stats.get(k, 0)
            report.sandbox = "; ".join(labels) + (
                f" — {totals['docker']} docker / {totals['host']} host / "
                f"{totals['blocked']} blocked command(s)")
        elif self.sandbox == "off":
            report.sandbox = "off (host execution)"
        # Purge AFTER the final grade: the verification run itself creates
        # pycache/.pytest_cache, and those must never enter the diff.
        _purge_caches(integration.path)
        try:
            report.diff = self.manager.diff(self.task_id)
        except Exception as e:
            report.note = (report.note + "; " if report.note else "") + \
                          f"final diff failed: {e}"
        try:
            report.commit = self.manager._git(["rev-parse", "HEAD"],
                                              integration.path)
        except Exception:
            pass
        if not report.outcomes:
            report.note = (report.note + "; " if report.note else "") + \
                          "the plan contains no tasks"
        broken = [o for o in report.outcomes if o.status != "succeeded"]
        report.success = (bool(report.final_verification)
                          and report.final_verification["passed"]
                          and not broken)
        if broken:
            report.note = (report.note + "; " if report.note else "") + \
                          ("tasks not succeeded: "
                           + ", ".join(f"{o.task_id}={o.status}" for o in broken))


def _purge_caches(path: Path) -> None:
    """Verification artifacts (pycache, pytest/mypy caches) are not
    source changes — purge them before diff/commit (the Phase 10 lesson)."""
    for cache in ("__pycache__", ".pytest_cache", ".mypy_cache"):
        for p in Path(path).rglob(cache):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
