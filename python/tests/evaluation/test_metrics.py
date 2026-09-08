"""Metric functions and aggregation on hand-built cases."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.evaluation import (  # noqa: E402
    TaskRunMetrics,
    aggregate,
    mrr,
    recall_at_k,
    topk_hit,
)


class TestRetrievalMetrics(unittest.TestCase):
    def test_recall_at_k(self):
        ranked = ["a.py", "b.py", "c.py", "d.py"]
        rel = {"b.py", "d.py"}
        self.assertEqual(recall_at_k(ranked, rel, 5), 1.0)
        self.assertEqual(recall_at_k(ranked, rel, 2), 0.5)   # only b in top-2
        self.assertEqual(recall_at_k(["x.py"], rel, 5), 0.0)
        self.assertEqual(recall_at_k([], {"a"}, 5), 0.0)
        self.assertEqual(recall_at_k(ranked, set(), 5), 1.0)  # nothing to find

    def test_mrr(self):
        self.assertEqual(mrr(["x", "y"], {"y"}), 0.5)
        self.assertEqual(mrr(["y", "x"], {"y"}), 1.0)
        self.assertEqual(mrr(["x"], {"z"}), 0.0)
        self.assertEqual(mrr([], {"z"}), 0.0)

    def test_topk_hit(self):
        self.assertEqual(topk_hit(["a", "b"], {"b"}, 2), 1.0)
        self.assertEqual(topk_hit(["a", "b"], {"b"}, 1), 0.0)


class TestAggregate(unittest.TestCase):
    def _run(self, task, resolved, passed, total, applied, repair=0,
             repair_ok=None):
        return TaskRunMetrics(task_id=task, category="bug_fix",
                              baseline="X", resolved=resolved,
                              tests_passed=passed, tests_total=total,
                              patch_applied=applied, repair_attempts=repair,
                              repair_success=repair_ok)

    def test_aggregate_rates(self):
        runs = [self._run("t1", True, 4, 4, True),
                self._run("t2", False, 2, 4, True),
                self._run("t3", True, 3, 3, False),
                self._run("t4", False, 1, 3, False)]
        a = aggregate(runs)
        self.assertEqual(a["resolve_rate"], 0.5)
        self.assertEqual(a["pass_at_1"], 0.5)
        self.assertEqual(a["patch_apply_rate"], 0.5)
        self.assertEqual(a["test_pass_rate"], 10 / 14)

    def test_repair_metrics_only_over_attempted_runs(self):
        runs = [self._run("t1", True, 4, 4, True, repair=1, repair_ok=True),
                self._run("t2", False, 2, 4, True, repair=3, repair_ok=False),
                self._run("t3", True, 3, 3, True)]
        a = aggregate(runs)
        self.assertEqual(a["repair_attempted"], 2)
        self.assertEqual(a["repair_success_rate"], 0.5)
        self.assertEqual(a["avg_repair_attempts"], 2.0)

    def test_empty_aggregate(self):
        self.assertEqual(aggregate([]), {})

    def test_retrieval_aggregation_when_present(self):
        runs = [TaskRunMetrics(task_id="t", category="x", baseline="grep",
                               recall5=0.5, recall10=1.0, mrr=1.0, topk_hit5=1.0)]
        a = aggregate(runs)
        self.assertEqual(a["recall@5"], 0.5)
        self.assertEqual(a["recall@10"], 1.0)
        self.assertEqual(a["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
