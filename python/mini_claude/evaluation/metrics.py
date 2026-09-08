"""Metric computation for the evaluation suite.

Software engineering: Resolve Rate, Pass@1, Patch Apply Rate, Test Pass
Rate. Retrieval: Recall@5/10, MRR, Top-K Hit. Efficiency: tokens, tool
calls, turns, latency, cost, files read/modified. Self-Repair: Repair
Success Rate, Average Repair Attempts."""

from __future__ import annotations

from dataclasses import dataclass, field


# ─── retrieval metrics ──────────────────────────────────────────

def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr(ranked: list[str], relevant: set[str]) -> float:
    for i, item in enumerate(ranked):
        if item in relevant:
            return 1.0 / (i + 1)
    return 0.0


def topk_hit(ranked: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & relevant else 0.0


# ─── per-run metrics ────────────────────────────────────────────

@dataclass
class TaskRunMetrics:
    task_id: str
    category: str
    baseline: str
    # software engineering
    resolved: bool = False
    tests_passed: int = 0
    tests_total: int = 0
    patch_applied: bool = False      # ≥1 file-modifying tool call, all clean
    files_modified: int = 0
    # retrieval (agentic relevance is not graded; filled by retrieval runs)
    recall5: float | None = None
    recall10: float | None = None
    mrr: float | None = None
    topk_hit5: float | None = None
    # efficiency
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    turns: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    files_read: int = 0
    # self-repair
    repair_attempts: int = 0
    repair_success: bool | None = None  # None = no repair needed/attempted
    # raw detail
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "category": self.category,
            "baseline": self.baseline, "resolved": self.resolved,
            "tests_passed": self.tests_passed, "tests_total": self.tests_total,
            "patch_applied": self.patch_applied,
            "files_modified": self.files_modified,
            "recall@5": self.recall5, "recall@10": self.recall10,
            "mrr": self.mrr, "topk_hit@5": self.topk_hit5,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls, "turns": self.turns,
            "latency_s": round(self.latency_s, 2),
            "cost_usd": round(self.cost_usd, 6),
            "files_read": self.files_read,
            "repair_attempts": self.repair_attempts,
            "repair_success": self.repair_success,
            "detail": self.detail,
        }


# ─── aggregation ───────────────────────────────────────────────

def aggregate(runs: list[TaskRunMetrics]) -> dict:
    """Suite-level summary over one baseline's (or config's) runs."""
    if not runs:
        return {}
    n = len(runs)
    resolved = sum(1 for r in runs if r.resolved)
    graded = sum(r.tests_total for r in runs)
    passed = sum(r.tests_passed for r in runs)
    applied = sum(1 for r in runs if r.patch_applied)
    retrieval = [r for r in runs if r.recall5 is not None]
    repairs = [r for r in runs if r.repair_attempts > 0]
    return {
        "tasks": n,
        # software engineering
        "resolve_rate": resolved / n,
        "pass_at_1": resolved / n,
        "patch_apply_rate": applied / n,
        "test_pass_rate": (passed / graded) if graded else 0.0,
        "resolved_count": resolved,
        "tests_passed_total": passed,
        "tests_total": graded,
        # retrieval
        "recall@5": sum(r.recall5 for r in retrieval) / len(retrieval)
                    if retrieval else None,
        "recall@10": sum(r.recall10 for r in retrieval) / len(retrieval)
                     if retrieval else None,
        "mrr": sum(r.mrr for r in retrieval) / len(retrieval) if retrieval else None,
        "topk_hit@5": sum(r.topk_hit5 for r in retrieval) / len(retrieval)
                      if retrieval else None,
        # efficiency
        "input_tokens_avg": sum(r.input_tokens for r in runs) / n,
        "output_tokens_avg": sum(r.output_tokens for r in runs) / n,
        "tool_calls_avg": sum(r.tool_calls for r in runs) / n,
        "turns_avg": sum(r.turns for r in runs) / n,
        "latency_s_avg": sum(r.latency_s for r in runs) / n,
        "cost_usd_total": sum(r.cost_usd for r in runs),
        "files_read_avg": sum(r.files_read for r in runs) / n,
        "files_modified_avg": sum(r.files_modified for r in runs) / n,
        # self-repair
        "repair_attempted": len(repairs),
        "repair_success_rate": (sum(1 for r in repairs if r.repair_success) / len(repairs))
                               if repairs else None,
        "avg_repair_attempts": (sum(r.repair_attempts for r in repairs) / len(repairs))
                               if repairs else None,
    }
