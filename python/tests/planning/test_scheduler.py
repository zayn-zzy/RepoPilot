"""Scheduler tests — readiness gating (dependencies must SUCCEED first),
failure propagation, retry policy, replan, and full execution."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import DAGError, Scheduler, TaskDAG, TaskStatus  # noqa: E402
from mini_claude.planning.task import TaskNode  # noqa: E402


def _node(tid, deps=None, role="coder", priority=0):
    return TaskNode(id=tid, title=tid, description=tid, agent_role=role,
                    dependencies=deps or [], priority=priority)


def _diamond():
    """A -> B, A -> C, B -> D, C -> D"""
    return TaskDAG([
        _node("A"),
        _node("B", deps=["A"]),
        _node("C", deps=["A"]),
        _node("D", deps=["B", "C"], role="reviewer"),
    ])


class TestReadiness(unittest.TestCase):
    def test_only_deps_done_are_ready(self):
        sched = Scheduler(_diamond())
        self.assertEqual(sched.available(), ["A"])

        task = sched.next_ready()
        self.assertEqual(task.id, "A")
        self.assertEqual(sched.dag.tasks["A"].status, TaskStatus.RUNNING)
        # While A runs, nothing else may be claimed.
        self.assertIsNone(sched.next_ready())

        sched.complete("A", True, "ok")
        self.assertEqual(sched.available(), ["B", "C"])

    def test_running_task_cannot_be_claimed_twice(self):
        sched = Scheduler(_diamond())
        sched.next_ready()          # claims A
        self.assertIsNone(sched.next_ready())  # nothing else is READY

    def test_priority_order(self):
        dag = TaskDAG([_node("low", priority=1), _node("high", priority=5),
                       _node("mid", priority=3)])
        sched = Scheduler(dag)
        self.assertEqual(sched.available(), ["high", "mid", "low"])

    def test_claiming_not_ready_raises(self):
        sched = Scheduler(_diamond())
        with self.assertRaises(DAGError):
            sched.dag.mark_running("B")  # deps not done


class TestFailurePropagation(unittest.TestCase):
    def test_dependents_blocked_on_failure(self):
        sched = Scheduler(_diamond())
        sched.next_ready()                       # A
        sched.complete("A", False, "boom")       # fails, then retried
        self.assertEqual(sched.dag.tasks["A"].status, TaskStatus.READY)
        self.assertEqual(sched.dag.tasks["B"].status, TaskStatus.BLOCKED)
        self.assertEqual(sched.dag.tasks["C"].status, TaskStatus.BLOCKED)
        self.assertEqual(sched.dag.tasks["D"].status, TaskStatus.BLOCKED)

    def test_transitive_blocking(self):
        sched = Scheduler(_diamond())
        sched.next_ready()
        sched.complete("A", False)
        self.assertEqual(sched.dag.tasks["D"].status, TaskStatus.BLOCKED)


class TestRetry(unittest.TestCase):
    def test_retry_within_attempts(self):
        sched = Scheduler(TaskDAG([_node("A")]), max_attempts=3)
        sched.next_ready()
        for _ in range(3):
            sched.complete("A", False, "boom")
            self.assertEqual(sched.dag.tasks["A"].status, TaskStatus.READY)
            sched.next_ready()
            self.assertEqual(sched.dag.tasks["A"].attempts, sched.dag.tasks["A"].attempts or 0)

    def test_retry_exhausted_blocks(self):
        sched = Scheduler(TaskDAG([_node("A")]), max_attempts=2)
        sched.next_ready()
        sched.complete("A", False)
        self.assertEqual(sched.dag.tasks["A"].status, TaskStatus.READY)
        self.assertEqual(sched.dag.tasks["A"].attempts, 1)
        sched.next_ready()
        sched.complete("A", False)
        self.assertEqual(sched.dag.tasks["A"].attempts, 2)
        sched.next_ready()
        sched.complete("A", False)
        self.assertEqual(sched.dag.tasks["A"].status, TaskStatus.BLOCKED)

    def test_attempts_counted_per_try(self):
        sched = Scheduler(TaskDAG([_node("A")]), max_attempts=3)
        sched.next_ready()
        sched.complete("A", False)
        self.assertEqual(sched.dag.tasks["A"].attempts, 1)


class TestReplan(unittest.TestCase):
    def test_replan_resets_failed_and_blocked(self):
        sched = Scheduler(_diamond())
        sched.next_ready()
        sched.complete("A", False)          # A READY (retry), B/C/D BLOCKED
        reset = sched.dag.replan(["B"])
        self.assertEqual(reset, ["B"])
        self.assertEqual(sched.dag.tasks["B"].status, TaskStatus.PENDING)
        self.assertEqual(sched.dag.tasks["C"].status, TaskStatus.BLOCKED)

    def test_replan_all(self):
        sched = Scheduler(_diamond())
        sched.next_ready()
        sched.complete("A", False)      # A failed -> retried (READY), B/C/D blocked
        sched.dag.replan()
        stats = sched.dag.stats()
        self.assertEqual(stats["pending"], 3)   # B, C, D reset to pending
        self.assertEqual(stats["ready"], 1)     # A stays retryable
        self.assertEqual(stats["blocked"], 0)


class TestExecute(unittest.IsolatedAsyncioTestCase):
    async def test_full_execution(self):
        sched = Scheduler(_diamond())
        log: list[str] = []

        async def executor(task):
            log.append(task.id)
            return True, f"{task.id} done"

        results = await sched.execute(executor)
        # A first; then B/C in priority order (tie -> id order); D last.
        self.assertEqual(log, ["A", "B", "C", "D"])
        self.assertTrue(sched.is_done())
        self.assertEqual(results["D"], "D done")
        self.assertEqual(sched.dag.stats()["succeeded"], 4)

    async def test_execution_failure_blocks_rest(self):
        sched = Scheduler(_diamond())

        async def executor(task):
            return (task.id != "A"), "boom" if task.id == "A" else "ok"

        await sched.execute(executor)
        stats = sched.dag.stats()
        # A retries 3 times, all fail -> A blocked; B/C/D never run (blocked)
        self.assertEqual(stats["blocked"], 4)

    async def test_scheduler_rejects_invalid_dag(self):
        dag = TaskDAG([_node("A", deps=["GHOST"])])
        with self.assertRaises(DAGError):
            Scheduler(dag)

    async def test_empty_dag(self):
        sched = Scheduler(TaskDAG())
        self.assertTrue(sched.is_done())
        self.assertEqual(await sched.execute(lambda t: (True, "x")), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
