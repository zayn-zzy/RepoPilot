"""Harness tests — real retrieval runs on generated repos, real grading,
and scripted-LLM agentic runs through the exact recording path."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.evaluation import (  # noqa: E402
    BASELINES,
    AgenticEvaluator,
    build_task_repos,
    grade,
    retrieval_eval,
)
from mini_claude.evaluation.tasks import apply_solution, build_task_repos  # noqa: E402


# ─── Fake Anthropic SDK boundary (same contract as the Phase 5 tests) ──

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
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _text_stream(text):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(text=text)),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=_usage()))


def _tool_use_stream(name, inp):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="t1", name=name)),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(partial_json=json.dumps(inp))),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name=name, input=dict(inp))],
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


FIX_MULTIPLY = [
    _tool_use_stream("read_file", {"file_path": "calc.py"}),
    _tool_use_stream("edit_file", {"file_path": "calc.py",
                                   "old_string": "def multiply(a, b):\n    return a + b",
                                   "new_string": "def multiply(a, b):\n    return a * b"}),
    _text_stream("Fixed multiply to multiply instead of adding."),
]
DO_NOTHING = [_text_stream("I have no idea what to do.")]


class TestRetrievalEval(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = build_task_repos(Path(self._tmp.name))

    def test_real_retrieval_over_all_stacks(self):
        runs = retrieval_eval(self.tasks)
        self.assertEqual(len(runs), 24 * 4)
        by_stack = {}
        for r in runs:
            self.assertIsNotNone(r.recall5)
            self.assertTrue(0.0 <= r.recall5 <= 1.0)
            self.assertTrue(0.0 <= r.mrr <= 1.0)
            by_stack.setdefault(r.baseline, []).append(r)
        self.assertEqual(set(by_stack), {"grep", "semantic", "hybrid",
                                         "hybrid+structural"})
        # The hybrid stacks find the calc-bug-multiply fix location: the
        # description names multiply() and calc.py holds it.
        hybrid = {r.task_id: r for r in by_stack["hybrid+structural"]}
        self.assertGreater(hybrid["calc-bug-multiply"].mrr, 0.0)
        self.assertIn("calc.py", hybrid["calc-bug-multiply"].detail["ranked"][:5])


class TestGrade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.task = [t for t in build_task_repos(Path(self._tmp.name))
                     if t.task_id == "calc-bug-multiply"][0]

    def test_grade_buggy_then_fixed(self):
        resolved, passed, total, summary = grade(self.task.root)
        self.assertFalse(resolved)
        self.assertGreater(total, 0)
        self.assertIn("test_multiply", summary)
        apply_solution(self.task)
        resolved, passed, total, summary = grade(self.task.root)
        self.assertTrue(resolved)
        self.assertEqual(passed, total)


class TestAgenticHarness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.task = [t for t in build_task_repos(self.tmp / "tasks")
                     if t.task_id == "calc-bug-multiply"][0]
        self.results = self.tmp / "results"

    def _evaluator(self, script):
        ev = AgenticEvaluator(model="mock-model", api_key="test-key",
                              results_dir=self.results)

        def patch(runtime):
            _install_scripted_llm(runtime, list(script))
        ev._after_build = patch
        return ev

    async def test_single_baseline_fixes_and_records_raw_json(self):
        ev = self._evaluator(FIX_MULTIPLY)
        run = await ev.run_one(self.task, BASELINES["A"])
        self.assertTrue(run.resolved)
        self.assertTrue(run.patch_applied)
        self.assertGreaterEqual(run.files_read, 1)
        self.assertEqual(run.files_modified, 1)
        self.assertGreater(run.tool_calls, 0)
        self.assertGreater(run.latency_s, 0.0)
        ev._save(self.task, BASELINES["A"], run)
        saved = json.loads((self.results / "calc-bug-multiply__A.json").read_text())
        self.assertTrue(saved["resolved"])
        self.assertEqual(saved["baseline"], "A")

    async def test_single_baseline_doing_nothing_is_not_resolved(self):
        ev = self._evaluator(DO_NOTHING)
        run = await ev.run_one(self.task, BASELINES["A"])
        self.assertFalse(run.resolved)
        self.assertFalse(run.patch_applied)
        self.assertEqual(run.files_modified, 0)

    async def test_team_stop_after_tester_skips_reviewer(self):
        """The -Reviewer ablation: the pipeline ends at the tester."""
        from mini_claude.agents import TeamConfig, TeamRunner
        from mini_claude.repo import RepositoryIndex

        idx = RepositoryIndex(self.task.root)
        idx.build()
        captured = []

        def after_build(runtime):
            captured.append(runtime.config.role)
            role = runtime.config.role
            _install_scripted_llm(runtime, [
                _tool_use_stream("publish_artifact", {
                    "kind": {"planner": "plan", "explorer": "exploration",
                             "coder": "code_change", "tester": "test_report",
                             "reviewer": "review"}[role],
                    "producer": role,
                    "payload": {"planner": {"tasks": [], "summary": "s",
                                            "related_files": ["calc.py"]},
                                "explorer": {"findings": [], "related_files": [],
                                             "symbols": []},
                                "coder": {"files_modified": [], "changes": [],
                                          "test_commands": []},
                                "tester": {"passed": True, "failed_tests": [],
                                           "summary": "ok"},
                                "reviewer": {"approved": True, "issues": [],
                                             "suggestions": []}}[role],
                }),
                _text_stream("done"),
            ])

        team = TeamRunner(TeamConfig(model="mock", index=idx, api_key="k",
                                     stop_after="tester"),
                          after_build=after_build)
        result = await team.run("fix something")
        self.assertEqual(captured, ["planner", "explorer", "coder", "tester"])
        self.assertEqual([o.role for o in result.outcomes],
                         ["planner", "explorer", "coder", "tester"])
        self.assertIsNone(result.approved)  # reviewer never ran


if __name__ == "__main__":
    unittest.main(verbosity=2)
