"""TaskNode and the task state machine.

Statuses flow: PENDING → READY → RUNNING → SUCCEEDED | FAILED;
FAILED → READY (retry) or BLOCKED (attempts exhausted / dependent of a
failure); BLOCKED → READY (replan). Every transition is checked against
ALLOWED_TRANSITIONS — invalid ones raise ValueError."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Task-level roles — the five multi-agent roles (Phase 5); 'general' is the
# unconstrained CLI role and is not a valid task assignee.
TASK_ROLES = ("planner", "explorer", "coder", "tester", "reviewer")

DEFAULT_MAX_ATTEMPTS = 3


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


TERMINAL_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED)

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.SKIPPED},
    TaskStatus.READY: {TaskStatus.RUNNING},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.PENDING},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.SKIPPED: set(),
}


@dataclass(frozen=True)
class TaskBudget:
    """Per-task spending limits (mirrors runtime.Budget limits)."""

    max_cost_usd: float | None = None
    max_turns: int | None = None


@dataclass
class TaskNode:
    """One node in the task DAG. `dependencies` holds task ids that must
    SUCCEED (or be SKIPPED) before this task may run."""

    id: str
    title: str
    description: str
    agent_role: str
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    files: list[str] = field(default_factory=list)
    budget: TaskBudget = field(default_factory=TaskBudget)
    attempts: int = 0
    result: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("task id must be non-empty")
        if self.agent_role not in TASK_ROLES:
            raise ValueError(
                f"unknown agent_role {self.agent_role!r} (expected one of {TASK_ROLES})"
            )
        if any(not d.strip() for d in self.dependencies):
            raise ValueError("dependency ids must be non-empty")
        if self.priority < 0:
            raise ValueError("priority must be >= 0")
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")

    def can_transition(self, new_status: TaskStatus) -> bool:
        return new_status in ALLOWED_TRANSITIONS[self.status]

    def transition(self, new_status: TaskStatus) -> None:
        if not self.can_transition(new_status):
            raise ValueError(
                f"invalid transition: {self.id} {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
