"""Evaluation harness — runs baselines over the task suite and saves RAW
per-run results (never just aggregate numbers; the raw JSON is the
experiment record).

Two real evaluation modes:

1. Retrieval evaluation (no LLM): each task's description is the query;
   the task's relevant_files (the canonical fix's files) is the ground
   truth. Baselines' retrieval stacks (grep / semantic / hybrid /
   hybrid+structural) rank the repo files; Recall@5/10, MRR and
   Top-K Hit are computed per task.

2. Agentic evaluation (real LLM): baseline A/B/C run a single coding
   agent restricted to the baseline's tool set; the Proposed stack runs
   the five-role team + verification + bounded self-repair. Each run is
   graded by running the repo's own test suite for real; every run is
   recorded as one JSON file with all raw metrics.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..retrieval.pipeline import HybridRetriever, grep_baseline
from ..verify import VerificationPipeline
from .baselines import BaselineConfig
from .metrics import TaskRunMetrics, mrr, recall_at_k, topk_hit
from .tasks import RepositoryTask

# ─── retrieval evaluation ──────────────────────────────────────

RETRIEVAL_CONFIGS = {
    "grep": dict(enable_lexical=False, enable_semantic=False,
                 enable_structural=False, rerank=False, grep=True),
    "semantic": dict(enable_lexical=False, enable_semantic=True,
                     enable_structural=False, rerank=False),
    "hybrid": dict(enable_lexical=True, enable_semantic=True,
                   enable_structural=False, rerank=True),
    "hybrid+structural": dict(enable_lexical=True, enable_semantic=True,
                              enable_structural=True, rerank=True),
}


def _repo_files(root: Path) -> dict[str, str]:
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "test" not in str(p.relative_to(root)).split("/")[0] \
                and p.suffix == ".py" and "__pycache__" not in str(p):
            files[str(p.relative_to(root))] = p.read_text(errors="replace")
    return files


def retrieval_eval(tasks: list[RepositoryTask],
                   stacks: list[str] | None = None) -> list[TaskRunMetrics]:
    """Real retrieval runs over every task; ground truth = relevant_files."""
    stacks = stacks or list(RETRIEVAL_CONFIGS)
    runs: list[TaskRunMetrics] = []
    for task in tasks:
        files = _repo_files(task.root)
        relevant = set(task.relevant_files)
        for stack in stacks:
            cfg = RETRIEVAL_CONFIGS[stack]
            if cfg.get("grep"):
                ranked = [h.file_path for h in
                          grep_baseline(files, task.description, top_k=10)]
            elif cfg["enable_lexical"] or cfg["enable_semantic"] \
                    or cfg["enable_structural"]:
                index = _index_for(task.root)
                retriever = HybridRetriever(
                    index, files=files,
                    enable_lexical=cfg["enable_lexical"],
                    enable_semantic=cfg["enable_semantic"],
                    enable_structural=cfg["enable_structural"],
                    rerank=cfg["rerank"],
                )
                ranked = [h.file_path for h in
                          retriever.retrieve(task.description, top_k=10)]
            else:
                ranked = []
            runs.append(TaskRunMetrics(
                task_id=task.task_id, category=task.category,
                baseline=stack,
                recall5=recall_at_k(ranked, relevant, 5),
                recall10=recall_at_k(ranked, relevant, 10),
                mrr=mrr(ranked, relevant),
                topk_hit5=topk_hit(ranked, relevant, 5),
                detail={"ranked": ranked, "relevant": sorted(relevant)},
            ))
    return runs


def _index_for(root: Path):
    from ..repo import RepositoryIndex
    index = RepositoryIndex(root)
    index.build()
    return index


# ─── grading ───────────────────────────────────────────────────

def grade(root: Path) -> tuple[bool, int, int, str]:
    """Run the repo's own test suite for real. Returns
    (resolved, tests_passed, tests_total, summary_text).

    Counts are parsed from every test stage's stdout — fail-fast may
    stop the pipeline at targeted/unit before regression runs, and
    those stages' pytest output still carries the counts."""
    report = VerificationPipeline(root).run()
    resolved = report.passed
    passed = total = 0
    summary = ""
    for stage in report.stages:
        if stage.stdout and stage.stdout.strip():
            summary = stage.stdout
        m = re.search(r"(\d+)\s+passed", summary)
        m2 = re.search(r"(\d+)\s+failed", summary)
        if m:
            passed = int(m.group(1))
            total = passed + (int(m2.group(1)) if m2 else 0)
    if not summary and report.first_failure is not None:
        summary = report.first_failure.summary()
    return resolved, passed, total, summary


# ─── agentic evaluation ────────────────────────────────────────

class _RunRecorder:
    """Subscribes to a runtime's events and counts file activity."""

    def __init__(self):
        self.files_read: set[str] = set()
        self.files_written: set[str] = set()
        self.tool_calls = 0
        self.edit_failures = 0
        self.edit_attempts = 0

    def attach(self, runtime) -> None:
        def on_call(data):
            self.tool_calls += 1
            name = data.get("tool_name", "")
            inp = data.get("input") or {}
            if name == "read_file":
                self.files_read.add(str(inp.get("file_path", "")))
            elif name in ("write_file", "edit_file"):
                self.edit_attempts += 1
                self.files_written.add(str(inp.get("file_path", "")))

        def on_result(data):
            name = data.get("tool_name", "")
            if name in ("write_file", "edit_file"):
                result = str(data.get("result", ""))
                if result.startswith(("Error", "Action denied")):
                    self.edit_failures += 1

        runtime.events.on("tool_call", on_call)
        runtime.events.on("tool_result", on_result)


class AgenticEvaluator:
    """Runs baseline configs over tasks with the real LLM and saves raw
    per-run JSON records."""

    def __init__(self, *, model: str, api_key: str | None = None,
                 anthropic_base_url: str | None = None,
                 results_dir: str | Path = "evaluation_results",
                 permission_mode: str = "acceptEdits",
                 max_cost_usd: float = 1.0, max_turns: int = 12,
                 repair_max_cost_usd: float = 1.0,
                 after_build=None):
        self.model = model
        self.api_key = api_key
        self.anthropic_base_url = anthropic_base_url
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.permission_mode = permission_mode
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.repair_max_cost_usd = repair_max_cost_usd
        # Invoked with every freshly built runtime (single agents AND team
        # roles) — tests inject scripted LLM clients here.
        self._after_build = after_build

    # ── public API ─────────────────────────────────────────────

    async def run_tasks(self, tasks: list[RepositoryTask],
                        configs: list[BaselineConfig],
                        agentic: bool = True) -> list[TaskRunMetrics]:
        runs = []
        for task in tasks:
            for cfg in configs:
                run = await self.run_one(task, cfg)
                runs.append(run)
                self._save(task, cfg, run)
        return runs

    async def run_one(self, task: RepositoryTask,
                      cfg: BaselineConfig) -> TaskRunMetrics:
        start = time.monotonic()
        if cfg.kind == "team":
            run = await self._run_team(task, cfg)
        else:
            run = await self._run_single(task, cfg)
        run.latency_s = time.monotonic() - start
        return run

    # ── single-agent baselines A/B/C ───────────────────────────

    async def _run_single(self, task: RepositoryTask,
                          cfg: BaselineConfig) -> TaskRunMetrics:
        from ..runtime import AgentRuntime, AgentConfig, ToolACL, \
            build_default_registry

        prompt = (
            "You are a coding agent working in a small Python repository. "
            "The repository has a defect. Your task:\n"
            f"{task.description}\n\n"
            "Fix the code so the repository's own test suite passes. You can "
            "search, read and edit files. Work step by step and make the "
            "minimal change that fixes the defect. End your turn with a short "
            "paragraph describing what you changed."
        )
        recorder = _RunRecorder()
        config = AgentConfig(
            role="general",
            model=self.model,
            api_key=self.api_key,
            custom_system_prompt="You are a minimal coding agent.",
            permission_mode=self.permission_mode,
            max_cost_usd=self.max_cost_usd,
            max_turns=self.max_turns,
            anthropic_base_url=self.anthropic_base_url,
            interactive=False,
        )
        runtime = AgentRuntime(
            config,
            registry=build_default_registry(),
            acl=ToolACL(allowed_tools=set(cfg.tools),
                        permission_mode=self.permission_mode),
        )
        if self._after_build is not None:
            self._after_build(runtime)
        recorder.attach(runtime)
        # File tools are cwd-relative — bind the process cwd to the task
        # repo (the Phase 6/7 discipline), restored afterwards.
        import os
        previous_cwd = os.getcwd()
        os.chdir(task.root)
        try:
            run = await runtime.run(prompt)
        finally:
            os.chdir(previous_cwd)
        await runtime.close()
        metrics = run.trace.metrics()
        return self._finish_run(
            task, cfg, metrics, recorder,
            extra={"final_text": run.text[:500]},
        )

    # ── the Proposed team stack ────────────────────────────────

    async def _run_team(self, task: RepositoryTask,
                        cfg: BaselineConfig) -> TaskRunMetrics:
        from ..agents import TeamConfig, TeamRunner
        from ..verify import SelfRepairEngine
        from ..repo import RepositoryIndex

        index = RepositoryIndex(task.root)
        index.build()
        recorders: dict[str, _RunRecorder] = {}
        team_metrics: list[dict] = []

        def after_build(runtime):
            rec = _RunRecorder()
            rec.attach(runtime)
            recorders[runtime.config.role] = rec
            if self._after_build is not None:
                self._after_build(runtime)

        stop_after = "tester" if not cfg.reviewer else None
        team = TeamRunner(TeamConfig(
            model=self.model, index=index, api_key=self.api_key,
            anthropic_base_url=self.anthropic_base_url,
            permission_mode=self.permission_mode,
            max_cost_usd=self.max_cost_usd, max_turns=self.max_turns,
            stop_after=stop_after,
        ), after_build=after_build)
        team_result = await team.run(task.description)
        for outcome in team_result.outcomes:
            team_metrics.append({
                "role": outcome.role,
                "artifact": outcome.artifact.to_dict()
                            if outcome.artifact is not None else None,
                "run": {
                    "tokens_in": outcome.run.tokens_in,
                    "tokens_out": outcome.run.tokens_out,
                    "turns": outcome.run.turns,
                    "cost_usd": outcome.run.cost_usd,
                },
            })

        # Verification gate.
        resolved, passed, total, summary = grade(task.root)
        repair_info = {"attempts": 0, "success": None}
        if not resolved and cfg.self_repair:
            failure = VerificationPipeline(task.root).run().first_failure
            if failure is not None:
                engine = SelfRepairEngine(
                    task.root, index=index, model=self.model,
                    api_key=self.api_key,
                    anthropic_base_url=self.anthropic_base_url,
                    permission_mode=self.permission_mode,
                    max_cost_usd=self.repair_max_cost_usd, max_turns=10,
                )
                repair = await engine.repair(failure)
                repair_info = {"attempts": len(repair.attempts),
                               "success": repair.fixed,
                               "total_cost_usd": repair.total_cost_usd,
                               "attempt_costs": [a.cost_usd
                                                 for a in repair.attempts]}
                resolved, passed, total, summary = grade(task.root)

        merged = _RunRecorder()
        for rec in recorders.values():
            merged.files_read |= rec.files_read
            merged.files_written |= rec.files_written
            merged.tool_calls += rec.tool_calls
            merged.edit_failures += rec.edit_failures
            merged.edit_attempts += rec.edit_attempts
        tokens_in = sum(m["run"]["tokens_in"] for m in team_metrics)
        tokens_out = sum(m["run"]["tokens_out"] for m in team_metrics)
        cost = sum(m["run"]["cost_usd"] for m in team_metrics) \
            + repair_info.get("total_cost_usd", 0.0)
        turns = sum(m["run"]["turns"] for m in team_metrics)
        run = self._finish_run(
            task, cfg,
            {"input_tokens": tokens_in, "output_tokens": tokens_out,
             "llm_calls": len(team_metrics), "tool_calls": merged.tool_calls,
             "turns": turns, "cost_usd": cost,
             "permission_denials": 0, "budget_exceeded": 0},
            merged,
            extra={"team": team_metrics, "repair": repair_info,
                   "approved": team_result.approved,
                   "roles_run": [o.role for o in team_result.outcomes]},
        )
        return run

    # ── shared finishing ───────────────────────────────────────

    def _finish_run(self, task: RepositoryTask, cfg: BaselineConfig,
                    metrics: dict, recorder: _RunRecorder,
                    extra: dict | None = None) -> TaskRunMetrics:
        resolved, passed, total, summary = grade(task.root)
        patch_applied = recorder.edit_attempts > 0 and recorder.edit_failures == 0
        return TaskRunMetrics(
            task_id=task.task_id, category=task.category, baseline=cfg.name,
            resolved=resolved, tests_passed=passed, tests_total=total,
            patch_applied=patch_applied,
            files_modified=len(recorder.files_written),
            input_tokens=int(metrics.get("input_tokens", 0)),
            output_tokens=int(metrics.get("output_tokens", 0)),
            tool_calls=int(metrics.get("tool_calls", 0)),
            turns=int(metrics.get("turns", 0)),
            cost_usd=float(metrics.get("cost_usd", 0.0)),
            files_read=len(recorder.files_read),
            repair_attempts=int((extra or {}).get("repair", {}).get("attempts", 0)),
            repair_success=(extra or {}).get("repair", {}).get("success"),
            detail={"summary": summary[:2000], **(extra or {})},
        )

    def _save(self, task: RepositoryTask, cfg: BaselineConfig,
              run: TaskRunMetrics) -> None:
        out = self.results_dir / f"{task.task_id}__{cfg.name}.json"
        out.write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
