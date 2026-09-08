"""Task-suite integrity: 24 tasks, 6 categories × 4; every task's repo
really FAILS its own test suite in the buggy state and really PASSES
after the canonical fix (real pytest runs)."""

import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.evaluation import (  # noqa: E402
    CATEGORIES,
    TASK_SPECS,
    build_task_repos,
)
from mini_claude.evaluation.tasks import apply_solution  # noqa: E402


class TestTaskSuite(unittest.TestCase):
    def test_24_tasks_6_categories_by_4(self):
        self.assertEqual(len(TASK_SPECS), 24)
        counts = Counter(s.category for s in TASK_SPECS)
        self.assertEqual(set(counts), set(CATEGORIES))
        self.assertTrue(all(c == 4 for c in counts.values()), counts)
        # Every task has ground-truth relevant files that exist in its repo.
        for spec in TASK_SPECS:
            self.assertTrue(spec.relevant_files)
            self.assertTrue(spec.description.strip())
            self.assertIn(spec.template, ("calc", "shop", "text"))

    def test_every_task_fails_buggy_and_passes_after_fix(self):
        """The suite is honestly solvable: real pytest FAILS on each task's
        buggy state and PASSES after the canonical fix."""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = build_task_repos(Path(tmp))
            self.assertEqual(len(tasks), 24)
            ok_buggy = ok_fixed = 0
            for task in tasks:
                p1 = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                                    cwd=str(task.root), capture_output=True,
                                    text=True, timeout=120)
                apply_solution(task)
                p2 = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                                    cwd=str(task.root), capture_output=True,
                                    text=True, timeout=120)
                ok_buggy += p1.returncode != 0
                ok_fixed += p2.returncode == 0
                if p1.returncode == 0 or p2.returncode != 0:
                    self.fail(f"{task.task_id}: buggy rc={p1.returncode}, "
                              f"fixed rc={p2.returncode}\n{p1.stdout}\n{p2.stdout}")
            self.assertEqual((ok_buggy, ok_fixed), (24, 24))


if __name__ == "__main__":
    unittest.main(verbosity=2)
