"""DagRunner tests — real git repos, real worktrees, real verification,
real pytest runs; only the LLM SDK boundary is scripted (the same fake
contract as the team tests): dependency visibility across task
worktrees, true parallelism (wall-clock), merge conflicts that never
overwrite, failure cascades to BLOCKED dependents, and the bounded
repair loop that turns a FAIL into a PASS."""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.execution import DagRunner  # noqa: E402
from mini_claude.planning import TaskDAG  # noqa: E402
from mini_claude.planning.dag import DAGError  # noqa: E402
from mini_claude.planning.task import TaskNode  # noqa: E402

CALC = ("def add(a, b):\n    return a + b\n\n\n"
        "def multiply(a, b):\n    return a + b\n")


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "calc.py").write_text(CALC)
    (repo / "stats.py").write_text("from calc import add\n\n\ndef total(xs):\n"
                                   "    return sum(xs)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


def _node(task_id: str, title: str, role: str, deps=(), desc: str = "",
          priority: int = 0) -> TaskNode:
    return TaskNode(id=task_id, title=title, description=desc or title,
                    agent_role=role, dependencies=list(deps), priority=priority)


# ─── Fake Anthropic SDK boundary (same contract as the team tests) ──

class _FakeStream:
    def __init__(self, events, final_message, delay: float = 0.0):
        self._events = list(events)
        self._final = final_message
        self._delay = delay
        self._slept = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._delay and not self._slept:
            self._slept = True
            await asyncio.sleep(self._delay)
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _text_stream(text: str, delay: float = 0.0):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(text=text)),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=_usage()),
        delay=delay)


def _tool_use_stream(name: str, inp: dict):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="t1",
                                                      name=name)),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(partial_json=json.dumps(inp))),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name=name,
                                 input=dict(inp))],
        usage=_usage()))


class _FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def stream(self, **params):
        self.calls += 1
        return self._script.pop(0)


def _install_scripted_llm(runtime, script):
    runtime.agent._anthropic_client = SimpleNamespace(messages=_FakeMessages(script))


class _Router:
    """Hands every freshly built runtime its scripted LLM: the first
    build per task is the task agent, later builds are repair coders."""

    def __init__(self):
        self.scripts = {}   # task_id -> {"main": [...], "repair": [[...], ...]}
        self.calls = {}

    def add(self, task_id: str, main: list, repair: list | None = None):
        self.scripts[task_id] = {"main": list(main),
                                 "repair": [list(s) for s in (repair or [])]}

    def __call__(self, runtime, task):
        idx = self.calls.get(task.id, 0)
        self.calls[task.id] = idx + 1
        bucket = self.scripts[task.id]
        if idx == 0:
            script = bucket["main"]
        else:
            script = bucket["repair"][min(idx - 1, len(bucket["repair"]) - 1)]
        _install_scripted_llm(runtime, list(script))


# Reusable task scripts.
def _write_new_file(path: str, content: str, done: str = "done",
                    delay: float = 0.0) -> list:
    return [_tool_use_stream("write_file", {"file_path": path,
                                            "content": content}),
            _text_stream(done, delay=delay)]


def _rewrite_calc(content: str, done: str = "done") -> list:
    return [_tool_use_stream("read_file", {"file_path": "calc.py"}),
            _tool_use_stream("write_file", {"file_path": "calc.py",
                                            "content": content}),
            _text_stream(done)]


def _runner(repo: Path, plan: TaskDAG, *, jobs: int = 1, task_id: str = "RUN1",
            after_build=None, commit: bool = True, **kwargs) -> DagRunner:
    kwargs.setdefault("max_repair_attempts", 1)
    return DagRunner(repo, plan, task_id=task_id, model="mock-model",
                     api_key="test-key", jobs=jobs, commit=commit,
                     after_build=after_build, **kwargs)


class TestDagRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))

    # ─── dependency + scheduling ─────────────────────────────

    async def test_dependency_changes_visible_in_dependent_worktree(self):
        """T2 branches from the integration branch AFTER T1 merged, so
        T2's worktree must contain T1's file."""
        dag = TaskDAG([
            _node("T001", "add a helper", "coder"),
            _node("T002", "use the helper", "coder", deps=["T001"]),
        ])
        router = _Router()
        router.add("T001", _write_new_file(
            "helpers.py", "def helper():\n    return 1\n"))
        router.add("T002", [
            _tool_use_stream("read_file", {"file_path": "helpers.py"}),
            _tool_use_stream("write_file", {"file_path": "result.txt",
                                            "content": "helpers visible\n"}),
            _text_stream("done"),
        ])

        report = await _runner(self.repo, dag, jobs=2,
                               after_build=router).run("add a helper")

        self.assertTrue(report.success, report.summarize())
        self.assertEqual([o.status for o in report.outcomes],
                         ["succeeded", "succeeded"])
        t2 = next(o for o in report.outcomes if o.task_id == "T002")
        # T1's change was present in T2's worktree before T2 ran.
        self.assertTrue((t2.worktree.path / "helpers.py").exists())
        # Both changes landed on the integration worktree.
        self.assertEqual((report.worktree.path / "helpers.py").read_text(),
                         "def helper():\n    return 1\n")
        self.assertEqual((report.worktree.path / "result.txt").read_text(),
                         "helpers visible\n")
        self.assertEqual(report.diff.all_files, ["helpers.py", "result.txt"])

    async def test_scheduler_runs_diamond_dag_in_topo_order(self):
        """T3 depends on both T1 and T2 and must see both changes."""
        dag = TaskDAG([
            _node("T001", "one", "coder"),
            _node("T002", "two", "coder"),
            _node("T003", "merge them", "coder", deps=["T001", "T002"]),
        ])
        router = _Router()
        router.add("T001", _write_new_file("one.py", "ONE = 1\n"))
        router.add("T002", _write_new_file("two.py", "TWO = 2\n"))
        router.add("T003", [
            _tool_use_stream("read_file", {"file_path": "one.py"}),
            _tool_use_stream("read_file", {"file_path": "two.py"}),
            _tool_use_stream("write_file", {"file_path": "both.txt",
                                            "content": "both seen\n"}),
            _text_stream("done"),
        ])
        report = await _runner(self.repo, dag, jobs=2,
                               after_build=router).run("diamond")

        self.assertTrue(report.success, report.summarize())
        t3 = next(o for o in report.outcomes if o.task_id == "T003")
        self.assertTrue((t3.worktree.path / "one.py").exists())
        self.assertTrue((t3.worktree.path / "two.py").exists())
        self.assertEqual(set(report.diff.all_files),
                         {"one.py", "two.py", "both.txt"})

    # ─── parallelism (wall-clock, real threads) ──────────────

    async def test_independent_tasks_run_in_parallel(self):
        """jobs=2 must finish two 1-second tasks markedly faster than
        jobs=1 on the same workload — proving real parallel worktrees."""
        async def plan_and_run(jobs: int, tmp: Path) -> float:
            repo = _make_repo(tmp)
            dag = TaskDAG([_node("T001", "slow one", "coder"),
                           _node("T002", "slow two", "coder")])
            router = _Router()
            router.add("T001", _write_new_file("one.py", "ONE = 1\n",
                                               delay=1.0))
            router.add("T002", _write_new_file("two.py", "TWO = 2\n",
                                               delay=1.0))
            start = time.monotonic()
            report = await _runner(repo, dag, jobs=jobs,
                                   after_build=router).run("parallel")
            elapsed = time.monotonic() - start
            assert report.success, report.summarize()
            return elapsed

        t_par = await plan_and_run(2, Path(self._tmp.name) / "par")
        t_seq = await plan_and_run(1, Path(self._tmp.name) / "seq")
        # Sequential needs both 1s sleeps back-to-back; parallel overlaps
        # them. 0.8s margin over the machine noise (verification etc.
        # runs in both cases).
        self.assertLess(t_par, t_seq - 0.8,
                        f"parallel={t_par:.2f}s sequential={t_seq:.2f}s")

    # ─── merge conflicts (禁止冲突时暴力覆盖代码) ─────────────

    async def test_merge_conflict_aborts_and_never_overwrites(self):
        """Two independent tasks edit the same new file. The second merge
        conflicts: it must abort, leave the integration worktree
        byte-identical, mark the task conflicted and fail the run."""
        dag = TaskDAG([
            _node("T001", "write one", "coder", priority=5),
            _node("T002", "write two", "coder", priority=1),
        ])
        router = _Router()
        router.add("T001", _write_new_file("a.py", "VALUE = 'one'\n"))
        router.add("T002", _write_new_file("a.py", "VALUE = 'two'\n",
                                           delay=0.5))  # merges second

        report = await _runner(self.repo, dag, jobs=2,
                               after_build=router).run("conflict")

        self.assertFalse(report.success)
        by_id = {o.task_id: o for o in report.outcomes}
        self.assertEqual(by_id["T001"].status, "succeeded")
        self.assertEqual(by_id["T002"].status, "conflict")
        self.assertIn("aborted", by_id["T002"].error)
        # The integration worktree keeps the first version — nothing was
        # force-overwritten — and is clean again after the abort.
        self.assertEqual((report.worktree.path / "a.py").read_text(),
                         "VALUE = 'one'\n")
        from mini_claude.worktree import WorktreeManager
        mgr = WorktreeManager(self.repo)
        self.assertTrue(mgr.status(report.task_id).clean)
        self.assertEqual([o.task_id for o in report.outcomes if o.status == "conflict"],
                         ["T002"])

    # ─── failure cascade + bounded repair ────────────────────

    async def test_failed_task_blocks_dependents_and_stays_unmerged(self):
        """T1 breaks the build and repair cannot fix it: T1 must FAIL
        (committed for inspection, never merged), T2 must be BLOCKED,
        the integration worktree must keep the original code."""
        dag = TaskDAG([
            _node("T001", "break calc", "coder"),
            _node("T002", "depends on T1", "coder", deps=["T001"]),
        ])
        broken = CALC.replace("return a + b", "return a - b")
        router = _Router()
        router.add("T001", _rewrite_calc(broken, done="broke it"),
                   repair=[[_text_stream("cannot fix it")]])
        router.add("T002", [])  # never runs

        report = await _runner(self.repo, dag, jobs=2,
                               after_build=router).run("break it")

        self.assertFalse(report.success)
        by_id = {o.task_id: o for o in report.outcomes}
        self.assertEqual(by_id["T001"].status, "failed")
        self.assertIn("verification failed", by_id["T001"].error)
        self.assertEqual(by_id["T001"].repair["attempts"], 1)
        self.assertEqual(by_id["T002"].status, "blocked")
        self.assertIn("blocked", by_id["T002"].error)
        # Broken code never entered the result.
        self.assertEqual((report.worktree.path / "calc.py").read_text(), CALC)

    async def test_repair_loop_turns_failure_into_pass(self):
        """The Phase 7 loop inside the DAG: FAIL → Diagnose → Repair →
        Re-test → PASS, per task."""
        dag = TaskDAG([_node("T001", "break then fix calc", "coder")])
        broken = CALC.replace("return a + b", "return a - b")
        router = _Router()
        router.add("T001", _rewrite_calc(broken, done="broke it"),
                   repair=[[_tool_use_stream("read_file",
                                             {"file_path": "calc.py"}),
                            _tool_use_stream("write_file",
                                             {"file_path": "calc.py",
                                              "content": CALC}),
                            _text_stream("root cause: wrong operator; fixed")]])

        report = await _runner(self.repo, dag, after_build=router).run("fix it")

        self.assertTrue(report.success, report.summarize())
        t1 = report.outcomes[0]
        self.assertEqual(t1.status, "succeeded")
        self.assertEqual(t1.repair["attempts"], 1)
        self.assertTrue(t1.repair["success"])
        self.assertEqual((report.worktree.path / "calc.py").read_text(), CALC)
        self.assertTrue(report.final_verification["passed"])

    # ─── plan validation + workspace safety ──────────────────

    async def test_invalid_plan_fails_before_any_worktree(self):
        cycle = TaskDAG([
            _node("T001", "one", "coder", deps=["T002"]),
            _node("T002", "two", "coder", deps=["T001"]),
        ])
        report = await _runner(self.repo, cycle).run("cycle")
        self.assertFalse(report.success)
        self.assertIn("invalid plan", report.note)
        self.assertIsNone(report.worktree)
        # No worktrees were created.
        self.assertFalse((self.repo / "worktrees").exists())

        bad_id = TaskDAG([_node("T 1", "bad id", "coder")])
        report2 = await _runner(self.repo, bad_id).run("bad id")
        self.assertFalse(report2.success)
        self.assertIn("branch names", report2.note)

    async def test_main_workspace_never_touched(self):
        dag = TaskDAG([_node("T001", "add a file", "coder")])
        router = _Router()
        router.add("T001", _write_new_file("newmod.py", "N = 1\n"))
        main_branch = _git(self.repo, "branch", "--show-current")
        head_before = _git(self.repo, "rev-parse", "HEAD")

        report = await _runner(self.repo, dag, jobs=2,
                               after_build=router).run("isolated")

        self.assertTrue(report.success, report.summarize())
        self.assertEqual(_git(self.repo, "branch", "--show-current"), main_branch)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head_before)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertFalse((self.repo / "newmod.py").exists())

    async def test_no_commit_leaves_worktrees_dirty_but_runs(self):
        dag = TaskDAG([_node("T001", "add a file", "coder")])
        router = _Router()
        router.add("T001", _write_new_file("draft.py", "D = 1\n"))
        report = await _runner(self.repo, dag, after_build=router,
                               commit=False).run("dry run")
        self.assertTrue(report.success, report.summarize())
        t1 = report.outcomes[0]
        self.assertEqual(t1.commit, "")
        # The task worktree still carries the change (uncommitted).
        self.assertEqual((t1.worktree.path / "draft.py").read_text(), "D = 1\n")
        # Nothing was merged into the integration worktree.
        self.assertFalse((report.worktree.path / "draft.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
