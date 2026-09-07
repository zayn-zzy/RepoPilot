"""TaskDAG, DAGValidator and the execution Scheduler.

The validator rejects duplicate task ids, dangling dependencies, cycles, and
any status that violates the state machine. The Scheduler only ever hands
out tasks whose dependencies have all SUCCEEDED (or SKIPPED), drives the
status transitions, cascades failures to dependents (BLOCKED), and applies
the retry / replan policies."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .task import (
    ALLOWED_TRANSITIONS,
    DEFAULT_MAX_ATTEMPTS,
    TERMINAL_STATUSES,
    TaskNode,
    TaskStatus,
)


class DAGError(ValueError):
    pass


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


class DAGValidator:
    """Structural and state-machine checks over a TaskDAG."""

    def validate(self, dag: "TaskDAG") -> ValidationReport:
        errors: list[str] = []

        # Duplicate task ids can't exist in the dict (add_task raises) — but
        # report any duplicated dependency edge targets for completeness.
        ids = set(dag.tasks)
        for task in dag.tasks.values():
            for dep in task.dependencies:
                if dep not in ids:
                    errors.append(f"{task.id}: missing dependency '{dep}'")
                if dep == task.id:
                    errors.append(f"{task.id}: self-dependency")

        cycle = dag.find_cycle()
        if cycle:
            errors.append(f"dependency cycle: {' -> '.join(cycle)}")

        for task in dag.tasks.values():
            if not ALLOWED_TRANSITIONS.get(task.status):
                errors.append(f"{task.id}: unknown status {task.status.value}")
        return ValidationReport(errors)


class TaskDAG:
    def __init__(self, tasks: list[TaskNode] | None = None):
        self.tasks: dict[str, TaskNode] = {}
        for t in tasks or []:
            self.add_task(t)

    # ─── Mutation ────────────────────────────────────────────

    def add_task(self, task: TaskNode) -> None:
        if task.id in self.tasks:
            raise DAGError(f"duplicate task id: {task.id}")
        self.tasks[task.id] = task

    def get(self, task_id: str) -> TaskNode | None:
        return self.tasks.get(task_id)

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks.values())

    # ─── Structure ───────────────────────────────────────────

    def dependencies_of(self, task_id: str) -> list[str]:
        return list(self.tasks[task_id].dependencies)

    def dependents_of(self, task_id: str) -> list[str]:
        return [t.id for t in self.tasks.values() if task_id in t.dependencies]

    def topological_order(self) -> list[str]:
        """Deterministic dependency-first order (Kahn's algorithm with a
        sorted ready queue). Raises DAGError on cycles."""
        indegree = {t_id: 0 for t_id in self.tasks}
        for task in self.tasks.values():
            for dep in task.dependencies:
                indegree[task.id] += 1
        ready = sorted(t_id for t_id, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dep_id in self.dependents_of(node):
                indegree[dep_id] -= 1
                if indegree[dep_id] == 0:
                    ready.append(dep_id)
                    ready.sort()
        if len(order) != len(self.tasks):
            raise DAGError("dependency cycle detected")
        return order

    def find_cycle(self) -> list[str] | None:
        """One cycle among tasks, or None."""
        visited: set[str] = set()
        in_stack: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> list[str] | None:
            visited.add(node)
            in_stack.add(node)
            stack.append(node)
            for dep in self.tasks[node].dependencies:
                if dep not in self.tasks:
                    continue  # dangling dependency — the validator reports it
                if dep not in visited:
                    cycle = dfs(dep)
                    if cycle:
                        return cycle
                elif dep in in_stack:
                    start = stack.index(dep)
                    return stack[start:] + [dep]
            stack.pop()
            in_stack.remove(node)
            return None

        for t_id in sorted(self.tasks):
            if t_id not in visited:
                cycle = dfs(t_id)
                if cycle:
                    return cycle
        return None

    def validate(self) -> ValidationReport:
        return DAGValidator().validate(self)

    # ─── Readiness ───────────────────────────────────────────

    def is_ready(self, task_id: str) -> bool:
        task = self.tasks[task_id]
        return (
            all(self.tasks[d].status in TERMINAL_STATUSES for d in task.dependencies)
            and task.status in (TaskStatus.PENDING, TaskStatus.READY)
        )

    def ready_tasks(self) -> list[str]:
        """Task ids that may run next, highest priority first."""
        ready = [t.id for t in self.tasks.values() if t.status is TaskStatus.READY]
        ready.sort(key=lambda t_id: (-self.tasks[t_id].priority, t_id))
        return ready

    # ─── Status transitions ──────────────────────────────────

    def refresh_ready(self) -> list[str]:
        """PENDING → READY for every task whose dependencies are done.
        Returns the ids that became ready (priority order)."""
        promoted: list[str] = []
        for task in self.tasks.values():
            if task.status is TaskStatus.PENDING and self.is_ready(task.id):
                task.transition(TaskStatus.READY)
                promoted.append(task.id)
        promoted.sort(key=lambda t_id: (-self.tasks[t_id].priority, t_id))
        return promoted

    def mark_running(self, task_id: str) -> None:
        task = self.tasks[task_id]
        if task.status is not TaskStatus.READY:
            raise DAGError(f"{task_id} is not READY ({task.status.value})")
        task.transition(TaskStatus.RUNNING)

    def mark_succeeded(self, task_id: str, result: str | None = None) -> None:
        task = self.tasks[task_id]
        if task.status is not TaskStatus.RUNNING:
            raise DAGError(f"{task_id} is not RUNNING ({task.status.value})")
        task.transition(TaskStatus.SUCCEEDED)
        if result is not None:
            task.result = result
        self.refresh_ready()

    def mark_failed(self, task_id: str, result: str | None = None) -> list[str]:
        """RUNNING → FAILED; the blocking cascades transitively — every
        not-yet-terminal task with a FAILED/BLOCKED dependency becomes
        BLOCKED (they can never run again without a replan). Returns the
        newly blocked task ids."""
        task = self.tasks[task_id]
        if task.status is not TaskStatus.RUNNING:
            raise DAGError(f"{task_id} is not RUNNING ({task.status.value})")
        task.transition(TaskStatus.FAILED)
        if result is not None:
            task.result = result

        blocked: list[str] = []
        while True:
            newly_blocked = [
                t.id for t in self.tasks.values()
                if t.status is TaskStatus.PENDING
                and any(self.tasks[d].status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
                        for d in t.dependencies)
            ]
            if not newly_blocked:
                break
            for t_id in newly_blocked:
                self.tasks[t_id].transition(TaskStatus.BLOCKED)
                blocked.append(t_id)
        return blocked

    def retry(self, task_id: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> bool:
        """FAILED → READY while attempts remain; False when exhausted."""
        task = self.tasks[task_id]
        if task.status is not TaskStatus.FAILED:
            raise DAGError(f"{task_id} is not FAILED ({task.status.value})")
        if task.attempts >= max_attempts:
            task.transition(TaskStatus.BLOCKED)
            return False
        task.attempts += 1
        task.transition(TaskStatus.READY)
        return True

    def replan(self, task_ids: list[str] | None = None) -> list[str]:
        """Reset FAILED/BLOCKED/SKIPPED tasks back to PENDING (a replan pass
        by the planner). Returns the reset ids."""
        targets = task_ids if task_ids is not None else [
            t.id for t in self.tasks.values()
            if t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.SKIPPED)
        ]
        for t_id in targets:
            self.tasks[t_id].transition(TaskStatus.PENDING)
        self.refresh_ready()
        return list(targets)

    def stats(self) -> dict[str, int]:
        out = {s.value: 0 for s in TaskStatus}
        for task in self.tasks.values():
            out[task.status.value] += 1
        return out


class Scheduler:
    """Drives a TaskDAG through execution. Only READY tasks (all deps done)
    are ever handed out; failures cascade to dependents; retry and replan
    follow the policies in dag."""

    def __init__(self, dag: TaskDAG, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.dag = dag
        self.max_attempts = max_attempts
        report = dag.validate()
        if not report.valid:
            raise DAGError(f"invalid DAG: {'; '.join(report.errors)}")
        dag.refresh_ready()

    def available(self) -> list[str]:
        return self.dag.ready_tasks()

    def is_done(self) -> bool:
        return all(t.status in TERMINAL_STATUSES or t.status is TaskStatus.BLOCKED
                   for t in self.dag.tasks.values())

    def next_ready(self) -> TaskNode | None:
        """Claim the highest-priority READY task (→ RUNNING)."""
        ready = self.dag.ready_tasks()
        if not ready:
            return None
        task = self.dag.tasks[ready[0]]
        self.dag.mark_running(task.id)
        return task

    def complete(self, task_id: str, success: bool, result: str | None = None) -> dict:
        """Report a task's outcome. Returns {'retried': bool, 'blocked': [...],
        'newly_ready': [...]}."""
        task = self.dag.tasks[task_id]
        if success:
            self.dag.mark_succeeded(task_id, result)
            return {"retried": False, "blocked": [],
                    "newly_ready": self.dag.ready_tasks()}
        blocked = self.dag.mark_failed(task_id, result)
        retried = False
        if self.dag.retry(task_id, max_attempts=self.max_attempts):
            retried = True
            # Dependents stay blocked until the retry succeeds.
        return {"retried": retried, "blocked": blocked,
                "newly_ready": self.dag.ready_tasks()}

    async def execute(self, executor) -> dict[str, str]:
        """Run the whole DAG: `executor(task) -> (success: bool, result: str)`
        is an async callable invoked only for READY tasks. Returns the final
        result strings per task id."""
        while True:
            self.dag.refresh_ready()
            task = self.next_ready()
            if task is None:
                break
            success, result = await executor(task)
            self.complete(task.id, success, result)
        return {t.id: (t.result or "") for t in self.dag.tasks.values()}
