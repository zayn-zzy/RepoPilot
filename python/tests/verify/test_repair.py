"""SelfRepairEngine tests — scripted-LLM coder against the real pipeline
on a tmp copy of the bug_repo fixture: FAIL → diagnose → repair →
targeted re-test, with the max-attempts bound."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.verify import SelfRepairEngine, VerificationPipeline  # noqa: E402

BUG_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "bug_repo"

# The unique multiply body — the exact text the scripted coder edits.
OLD_MULTIPLY = ("def multiply(a, b):\n"
                "    # INJECTED BUG: multiplies by adding. The unit test below must fail.\n"
                "    return a + b\n")
NEW_MULTIPLY = "def multiply(a, b):\n    return a * b\n"


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


def _fix_script():
    """The coder's tool script: read the file (edit_file requires a prior
    read), apply the unique edit, then explain."""
    return [
        _tool_use_stream("read_file", {"file_path": "pkg/utils.py"}),
        _tool_use_stream("edit_file", {"file_path": "pkg/utils.py",
                                       "old_string": OLD_MULTIPLY,
                                       "new_string": NEW_MULTIPLY}),
        _text_stream("Root cause: multiply() added a+b instead of multiplying. "
                     "Changed its body to return a * b."),
    ]


def _do_nothing_script():
    return [_text_stream("I cannot figure out how to fix this.")]


class TestSelfRepair(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "bug_repo"
        # Never copy pycache — stale assertion-rewrite pyc files would make
        # pytest tracebacks reference the fixture's original paths.
        shutil.copytree(BUG_REPO, self.repo,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        self.pipeline = VerificationPipeline(self.repo, changed_files=["pkg/utils.py"])

    def _failure(self):
        report = self.pipeline.run()
        self.assertFalse(report.passed)
        self.assertEqual(report.first_failure.stage, "targeted_test")
        return report.first_failure

    def _engine(self, scripts_by_attempt):
        """Build an engine whose coder runtimes get scripted LLM clients,
        one script per attempt."""
        calls = {"n": 0}

        def after_build(runtime):
            script = scripts_by_attempt[calls["n"] % len(scripts_by_attempt)]
            calls["n"] += 1
            _install_scripted_llm(runtime, script)

        return SelfRepairEngine(self.repo, pipeline=self.pipeline,
                                api_key="test-key", after_build=after_build)

    async def test_repair_fixes_bug_in_one_attempt(self):
        failure = self._failure()
        result = await self._engine([_fix_script()]).repair(failure)

        self.assertTrue(result.fixed)
        self.assertEqual(len(result.attempts), 1)
        # The targeted re-test really passed.
        self.assertTrue(result.attempts[0].re_test.passed)
        # The coder really changed the file.
        self.assertIn("return a * b", (self.repo / "pkg/utils.py").read_text())
        self.assertIn("Root cause", result.attempts[0].outcome_text)
        # The full pipeline is green now.
        self.assertTrue(VerificationPipeline(
            self.repo, changed_files=["pkg/utils.py"]).run().passed)

    async def test_repair_stops_after_three_failed_attempts(self):
        failure = self._failure()
        result = await self._engine([_do_nothing_script()]).repair(failure)

        self.assertFalse(result.fixed)
        self.assertEqual(len(result.attempts), 3)  # max_repair_attempts = 3
        self.assertTrue(all(not a.fixed for a in result.attempts))
        # The bug is still there (no silent "success").
        self.assertIn("return a + b", (self.repo / "pkg/utils.py").read_text())

    async def test_repair_succeeds_on_second_attempt(self):
        failure = self._failure()
        result = await self._engine([_do_nothing_script(), _fix_script()]).repair(failure)

        self.assertTrue(result.fixed)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].fixed)
        self.assertTrue(result.attempts[1].fixed)

    async def test_prompt_contains_summary_and_related_code(self):
        failure = self._failure()
        result = await self._engine([_do_nothing_script()]).repair(failure)
        prompt = result.attempts[0].prompt
        # Failure Summarizer output.
        self.assertIn("Stage: targeted_test", prompt)
        self.assertIn("test_multiply", prompt)
        self.assertIn("Exit code: 1", prompt)
        # Retrieve Related Code: the failed test file and the changed file.
        self.assertIn("def multiply", prompt)
        self.assertIn("INJECTED BUG", prompt)
        self.assertIn("def test_multiply", prompt)

    async def test_previous_attempts_are_in_the_prompt(self):
        failure = self._failure()
        result = await self._engine([_do_nothing_script()]).repair(failure)
        prompt = result.attempts[1].prompt
        self.assertIn("Previous attempts", prompt)
        self.assertIn("cannot figure out", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
