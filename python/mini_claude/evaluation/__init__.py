"""Phase 9 — Evaluation + Benchmark.

A 24-task repository suite (6 categories × 4), four baselines
(A: original agent + grep/read/edit, B: + semantic retrieval,
C: + hybrid retrieval, Proposed: + structural + multi-agent +
verification + self-repair), the ablation matrix, and a harness that
computes the engineering/retrieval/efficiency/self-repair metrics and
SAVES RAW per-run results (the spec forbids reporting only final
numbers without the experiment data)."""

from .baselines import ABLATIONS, BASELINES, BaselineConfig  # noqa: F401
from .harness import AgenticEvaluator, grade, retrieval_eval  # noqa: F401
from .metrics import (  # noqa: F401
    TaskRunMetrics,
    aggregate,
    mrr,
    recall_at_k,
    topk_hit,
)
from .tasks import (  # noqa: F401
    CATEGORIES,
    TASK_SPECS,
    RepositoryTask,
    TaskSpec,
    build_task_repos,
)

__all__ = [
    "TASK_SPECS", "CATEGORIES", "TaskSpec", "RepositoryTask",
    "build_task_repos",
    "BASELINES", "ABLATIONS", "BaselineConfig",
    "retrieval_eval", "AgenticEvaluator", "grade",
    "TaskRunMetrics", "aggregate", "recall_at_k", "mrr", "topk_hit",
]
