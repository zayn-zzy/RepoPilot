"""Application Service tests (Web Phase 1) — the use-case layer the CLI
and the Web API share: no prints, structured results, same code path as
the CLI. The RunService test drives a real DagRunner with a scripted
LLM (the established contract), proving a run can be made entirely
without the CLI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.application import (  # noqa: E402
    ApplicationError,
    AskService,
    BenchmarkService,
    GraphService,
    PlanningService,
    RepositoryService,
    RunService,
    plan_to_web,
)

CALC = ("def add(a, b):\n    return a + b\n\n\n"
        "def multiply(a, b):\n    return a + b\n")
FIXED_CALC = ("def add(a, b):\n    return a + b\n\n\n"
              "def multiply(a, b):\n    return a * b\n")


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
    (repo / "calc.py").write_text(CALC)
    (repo / "stats.py").write_text("from calc import add\n\n\ndef total(xs):\n"
                                   "    return add(sum(xs), 0)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_multiply():\n    assert multiply(3, 4) == 12\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


class TestRepositoryService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.svc = RepositoryService()

    def test_initialize_on_git_and_non_git(self):
        repo = _make_repo(Path(self._tmp.name))
        result = self.svc.initialize(repo)
        self.assertTrue(result.ok)
        self.assertTrue(result.config_path.is_file())
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        result = self.svc.initialize(plain)
        self.assertFalse(result.ok)
        self.assertIn("not a git repository", result.error)

    def test_index_and_status_roundtrip(self):
        repo = _make_repo(Path(self._tmp.name))
        result = self.svc.index(repo)
        self.assertEqual(result.files, 3)
        self.assertIn("built fresh", result.note)
        self.assertTrue(result.db_path.is_file())
        # re-index is incremental (load reuse)
        result2 = self.svc.index(repo)
        self.assertIn("up to date", result2.note)
        status = self.svc.index_status(repo)
        self.assertTrue(status.exists)
        self.assertEqual(status.files, 3)
        self.assertGreater(status.symbols, 0)
        self.assertGreater(status.size_bytes, 0)
        self.assertGreater(status.saved_at, 0)

    def test_index_status_without_db_is_honest(self):
        repo = _make_repo(Path(self._tmp.name))
        status = self.svc.index_status(repo)
        self.assertFalse(status.exists)

    def test_index_missing_path_raises_application_error(self):
        with self.assertRaises(ApplicationError) as ctx:
            self.svc.index(Path(self._tmp.name) / "nope")
        self.assertEqual(ctx.exception.code, "REPOSITORY_NOT_FOUND")


class TestAskService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        RepositoryService().index(self.repo)

    def test_ask_returns_structured_evidence_without_key(self):
        result = AskService().ask(self.repo, "where is multiply defined",
                                  semantic="lsa", answer=True)  # no api_key
        self.assertFalse(result.has_answer)
        self.assertIn("lsa", result.semantic_label)
        self.assertTrue(result.context)
        self.assertTrue(result.hits)
        hit = result.hits[0]
        self.assertTrue(hit.file_path.endswith("calc.py"))
        self.assertIn("semantic", hit.sources)   # honest per-source scores
        self.assertGreater(hit.score, 0)


class TestPlanningService(unittest.TestCase):
    def test_deterministic_plan_is_structured(self):
        result = PlanningService().create_plan(
            Path("/unused"), text="fix the multiply bug in calc.py")
        self.assertEqual(result.planner, "deterministic")
        self.assertEqual(result.requirement.title.lower()[:4], "fix ")
        self.assertTrue(result.nodes)
        first = result.nodes[0]
        self.assertEqual(set(first), {"id", "agent_role", "title",
                                      "description", "dependencies",
                                      "priority", "files", "budget"})
        self.assertEqual(first["agent_role"], "coder")
        # edges follow dependencies
        for edge in result.edges:
            self.assertIn(edge["target"], {n["id"] for n in result.nodes})
            self.assertIn(edge["source"], {n["id"] for n in result.nodes})

    def test_plan_to_web_edges_match_dependencies(self):
        from mini_claude.planning import Planner
        plan = Planner()._deterministic_plan(
            PlanningService().parse("add a login feature"))
        nodes, edges = plan_to_web(plan)
        expected = {e["target"]: set() for e in edges}
        for e in edges:
            expected[e["target"]].add(e["source"])
        by_id = {n["id"]: n for n in nodes}
        for nid, sources in expected.items():
            self.assertEqual(sources, set(by_id[nid]["dependencies"]))

    def test_llm_plan_without_key_is_an_application_error(self):
        with self.assertRaises(ApplicationError) as ctx:
            PlanningService().create_plan(Path("/unused"), text="x", llm=True)
        self.assertEqual(ctx.exception.code, "API_KEY_REQUIRED")


class TestGraphService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        RepositoryService().index(self.repo)

    def test_graph_returns_modules_and_links(self):
        result = GraphService().graph(self.repo)
        self.assertIn("stats", result.modules)
        self.assertIn("calc", result.modules)
        self.assertTrue(any(link["source"] == "stats"
                            and link["target"] == "calc"
                            for link in result.links))
        self.assertIn("loaded", result.index_note)

    def test_graph_root_depth_limit(self):
        result = GraphService().graph(self.repo, root="stats", depth=1)
        self.assertIn("stats", result.modules)
        self.assertIn("calc", result.modules)   # 1 hop
        small = GraphService().graph(self.repo, root="stats", limit=1)
        self.assertTrue(small.truncated)
        self.assertEqual(len(small.modules), 1)

    def test_graph_unknown_root_is_an_application_error(self):
        with self.assertRaises(ApplicationError) as ctx:
            GraphService().graph(self.repo, root="no.such.module")
        self.assertEqual(ctx.exception.code, "MODULE_NOT_FOUND")


class TestBenchmarkService(unittest.TestCase):
    def test_subset_benchmark_returns_real_metrics(self):
        from mini_claude.evaluation import TASK_SPECS, build_task_repos
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tasks = build_task_repos(Path(tmp.name))[:2]
        result = BenchmarkService().run_retrieval_benchmark(
            semantic="lsa", tasks=tasks)
        self.assertEqual(len(result.runs), 2 * 5)   # real runs, raw rows
        self.assertEqual(set(result.by_stack),
                         {"grep", "semantic", "hybrid",
                          "lexical+structural (-Semantic)",
                          "hybrid+structural (Full)"})
        for metrics in result.by_stack.values():
            self.assertIn("recall@5", metrics)
            self.assertIn("mrr", metrics)
        self.assertIn("lsa", result.semantic_label)


# ─── scripted LLM (same contract as the execution tests) ──────

class _FakeStream:
    def __init__(self, events, final_message):
        self._events = list(events)
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20,
                           cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)


def _text_stream(text: str):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(text=text)),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=_usage()))


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


class TestRunService(unittest.TestCase):
    """A full run driven through the service — no CLI involved."""

    def test_run_through_service_succeeds(self):
        import asyncio
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = _make_repo(Path(tmp.name))

        from mini_claude.planning import TaskDAG
        from mini_claude.planning.task import TaskNode

        dag = TaskDAG([TaskNode(id="T001", title="fix multiply",
                                description="multiply must return a*b",
                                agent_role="coder")])
        requirement = PlanningService().parse("fix the multiply bug")

        def after_build(runtime, task):
            _install_scripted_llm(runtime, [
                _tool_use_stream("read_file", {"file_path": "calc.py"}),
                _tool_use_stream("write_file", {"file_path": "calc.py",
                                                "content": FIXED_CALC}),
                _text_stream("done"),
            ])

        result = asyncio.run(RunService().run(
            repo, requirement, plan=dag, task_id="SVC1",
            model="mock-model", api_key="test-key",
            jobs=1, sandbox="off", after_build=after_build))
        self.assertTrue(result.success, result.report.summarize())
        self.assertEqual(result.task_id, "SVC1")
        self.assertIsNotNone(result.report.pr)    # structured PR info
        self.assertIsNone(result.github)          # no GitHub opts
        self.assertTrue(result.pr_file.is_file())  # PR body written like the CLI
        self.assertIn("## Summary", result.pr_file.read_text())

    def test_run_without_key_is_an_application_error(self):
        import asyncio
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = _make_repo(Path(tmp.name))
        with self.assertRaises(ApplicationError) as ctx:
            asyncio.run(RunService().run(
                repo, PlanningService().parse("x"), plan=None))
        self.assertEqual(ctx.exception.code, "API_KEY_REQUIRED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
