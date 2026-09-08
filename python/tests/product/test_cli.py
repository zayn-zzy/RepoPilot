"""CLI tests — the real `repopilot` entry through subprocesses on tmp
git repos (init/index/graph/plan/benchmark/run-failure paths)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
CLI = [sys.executable, "-m", "mini_claude.product.cli"]


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n"
        "    return a + b\n")  # buggy but irrelevant for these commands
    (repo / "stats.py").write_text("from calc import add\n\n\ndef total(xs):\n"
                                   "    return sum(xs)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


def _run(cwd: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"),
         "PYTHONPATH": str(_PYTHON_DIR)}
    if env:
        e.update(env)
    return subprocess.run([*CLI, *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=300, env=e)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))

    def test_init_creates_repopilot_config(self):
        p = _run(self.repo, "init")
        self.assertEqual(p.returncode, 0, p.stderr)
        cfg = self.repo / ".repopilot" / "config.json"
        self.assertTrue(cfg.exists())
        data = json.loads(cfg.read_text())
        self.assertIn("repopilot_version", data)
        self.assertEqual(Path(data["root"]), self.repo.resolve())

    def test_init_refuses_non_git_directory(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        p = _run(plain, "init")
        self.assertEqual(p.returncode, 1)
        self.assertIn("not a git repository", p.stderr)

    def test_index_builds_and_saves_db(self):
        p = _run(self.repo, "index")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("indexed 3 files", p.stdout)
        self.assertTrue((self.repo / ".repopilot" / "index.db").exists())

    def test_graph_prints_edges(self):
        p = _run(self.repo, "graph")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("modules:", p.stdout)
        self.assertIn("stats -> calc", p.stdout)

    def test_plan_deterministic_without_key(self):
        p = _run(self.repo, "plan",
                 "multiply() computes a+b instead of a*b — a bug.")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("requirement:", p.stdout)
        self.assertIn("--- plan ---", p.stdout)

    def test_run_without_key_fails_explicitly(self):
        p = _run(self.repo, "run", "fix the multiply bug")
        self.assertEqual(p.returncode, 1)
        self.assertIn("ANTHROPIC_API_KEY", p.stderr)

    def test_benchmark_runs_real_retrieval_and_saves_raw(self):
        p = _run(self.repo, "benchmark", "--out",
                 str(self.repo / ".repopilot" / "bench"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("MRR=", p.stdout)
        raw = self.repo / ".repopilot" / "bench" / "retrieval_results.json"
        self.assertTrue(raw.exists())
        rows = json.loads(raw.read_text())
        self.assertEqual(len(rows), 24 * 5)  # real runs, raw rows


if __name__ == "__main__":
    unittest.main(verbosity=2)
