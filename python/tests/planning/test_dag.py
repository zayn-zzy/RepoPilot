"""TaskDAG and DAGValidator tests — duplicate ids, missing dependencies,
cycles, topological ordering."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import DAGError, TaskDAG  # noqa: E402
from mini_claude.planning.task import TaskNode  # noqa: E402


def _node(tid, deps=None, role="coder"):
    return TaskNode(id=tid, title=tid, description=tid, agent_role=role,
                    dependencies=deps or [])


def _chain_dag(n=3):
    """T1 -> T2 -> ... -> Tn"""
    tasks = [_node(f"T{i}", deps=[f"T{i-1}"] if i > 1 else []) for i in range(1, n + 1)]
    return TaskDAG(tasks)


class TestTaskDAG(unittest.TestCase):
    def test_add_and_get(self):
        dag = _chain_dag(2)
        self.assertEqual(len(dag), 2)
        self.assertEqual(dag.get("T1").title, "T1")
        self.assertIsNone(dag.get("NOPE"))

    def test_duplicate_id_rejected(self):
        dag = _chain_dag(2)
        with self.assertRaises(DAGError):
            dag.add_task(_node("T1"))

    def test_dependencies_and_dependents(self):
        dag = _chain_dag(3)
        self.assertEqual(dag.dependencies_of("T3"), ["T2"])
        self.assertEqual(dag.dependents_of("T2"), ["T3"])

    def test_topological_order(self):
        dag = _chain_dag(4)
        self.assertEqual(dag.topological_order(), ["T1", "T2", "T3", "T4"])

    def test_topological_order_with_branches_deterministic(self):
        dag = TaskDAG([
            _node("A"), _node("B"), _node("C", deps=["A"]), _node("D", deps=["A", "B"]),
        ])
        order = dag.topological_order()
        self.assertEqual(order[0], "A")
        self.assertEqual(order[1], "B")
        self.assertEqual(set(order), {"A", "B", "C", "D"})
        self.assertLess(order.index("A"), order.index("C"))
        self.assertLess(order.index("A"), order.index("D"))
        self.assertLess(order.index("B"), order.index("D"))

    def test_find_cycle(self):
        dag = TaskDAG([_node("A", deps=["B"]), _node("B", deps=["A"])])
        cycle = dag.find_cycle()
        self.assertIsNotNone(cycle)
        self.assertEqual(set(cycle), {"A", "B"})

    def test_no_cycle(self):
        self.assertIsNone(_chain_dag(3).find_cycle())

    def test_self_dependency_detected(self):
        dag = TaskDAG([_node("A", deps=["A"])])
        self.assertIsNotNone(dag.find_cycle())
        self.assertFalse(dag.validate().valid)


class TestValidation(unittest.TestCase):
    def test_valid_dag(self):
        self.assertTrue(_chain_dag(3).validate().valid)

    def test_missing_dependency(self):
        dag = TaskDAG([_node("A"), _node("B", deps=["GHOST"])])
        report = dag.validate()
        self.assertFalse(report.valid)
        self.assertIn("missing dependency 'GHOST'", report.errors[0])

    def test_cycle_reported(self):
        dag = TaskDAG([_node("A", deps=["B"]), _node("B", deps=["A"])])
        report = dag.validate()
        self.assertFalse(report.valid)
        self.assertTrue(any("cycle" in e for e in report.errors))

    def test_multiple_errors_collected(self):
        dag = TaskDAG([_node("A", deps=["X"]), _node("B", deps=["Y", "B"])])
        report = dag.validate()
        self.assertGreaterEqual(len(report.errors), 2)

    def test_empty_dag_valid(self):
        self.assertTrue(TaskDAG().validate().valid)

    def test_topological_order_raises_on_cycle(self):
        dag = TaskDAG([_node("A", deps=["B"]), _node("B", deps=["A"])])
        with self.assertRaises(DAGError):
            dag.topological_order()


if __name__ == "__main__":
    unittest.main(verbosity=2)
