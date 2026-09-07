"""TaskNode tests — required fields and the transition state machine."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import TaskBudget, TaskNode, TaskStatus  # noqa: E402


def _task(**overrides):
    kw = dict(id="T1", title="t", description="d", agent_role="coder")
    kw.update(overrides)
    return TaskNode(**kw)


class TestTaskFields(unittest.TestCase):
    def test_all_required_fields_present(self):
        t = _task(dependencies=["T0"], priority=2, files=["a.py"],
                  budget=TaskBudget(max_cost_usd=0.5, max_turns=10))
        self.assertEqual(t.id, "T1")
        self.assertEqual(t.title, "t")
        self.assertEqual(t.description, "d")
        self.assertEqual(t.agent_role, "coder")
        self.assertEqual(t.dependencies, ["T0"])
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.priority, 2)
        self.assertEqual(t.files, ["a.py"])
        self.assertEqual(t.budget.max_cost_usd, 0.5)
        self.assertEqual(t.budget.max_turns, 10)

    def test_empty_id_rejected(self):
        with self.assertRaises(ValueError):
            _task(id="  ")

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            _task(agent_role="manager")

    def test_all_five_roles_accepted(self):
        for role in ("planner", "explorer", "coder", "tester", "reviewer"):
            _task(agent_role=role)

    def test_negative_priority_rejected(self):
        with self.assertRaises(ValueError):
            _task(priority=-1)

    def test_empty_dependency_rejected(self):
        with self.assertRaises(ValueError):
            _task(dependencies=[""])


class TestStateMachine(unittest.TestCase):
    def test_happy_path(self):
        t = _task()
        t.transition(TaskStatus.READY)
        t.transition(TaskStatus.RUNNING)
        t.transition(TaskStatus.SUCCEEDED)
        self.assertEqual(t.status, TaskStatus.SUCCEEDED)

    def test_failure_path_with_retry(self):
        t = _task()
        t.transition(TaskStatus.READY)
        t.transition(TaskStatus.RUNNING)
        t.transition(TaskStatus.FAILED)
        t.transition(TaskStatus.READY)     # retry
        t.transition(TaskStatus.RUNNING)
        t.transition(TaskStatus.FAILED)
        t.transition(TaskStatus.BLOCKED)   # attempts exhausted
        self.assertEqual(t.status, TaskStatus.BLOCKED)

    def test_invalid_transitions_rejected(self):
        cases = [
            (TaskStatus.PENDING, TaskStatus.SUCCEEDED),   # skip RUNNING
            (TaskStatus.PENDING, TaskStatus.FAILED),
            (TaskStatus.READY, TaskStatus.SUCCEEDED),     # skip RUNNING
            (TaskStatus.RUNNING, TaskStatus.READY),       # backwards
            (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),   # terminal
            (TaskStatus.SKIPPED, TaskStatus.RUNNING),
            (TaskStatus.BLOCKED, TaskStatus.RUNNING),
        ]
        for start, bad in cases:
            t = _task()
            # jump directly into `start` for the test via allowed paths
            t.status = start
            with self.assertRaises(ValueError, msg=f"{start} -> {bad} must fail"):
                t.transition(bad)

    def test_can_transition(self):
        t = _task()
        self.assertTrue(t.can_transition(TaskStatus.READY))
        self.assertFalse(t.can_transition(TaskStatus.RUNNING))

    def test_blocked_can_return_to_pending_for_replan(self):
        t = _task()
        t.status = TaskStatus.BLOCKED
        t.transition(TaskStatus.PENDING)
        self.assertEqual(t.status, TaskStatus.PENDING)

    def test_invalid_transition_message_mentions_task(self):
        t = _task()
        with self.assertRaises(ValueError) as ctx:
            t.transition(TaskStatus.SUCCEEDED)
        self.assertIn("T1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
