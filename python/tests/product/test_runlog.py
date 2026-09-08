"""RunLog (§24 observability) tests — scripted runtime through the real
recording path, and the JSONL store roundtrip."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.product import RunLogger, RunRecorder  # noqa: E402
from mini_claude.runtime import (  # noqa: E402
    AgentConfig,
    AgentRuntime,
    build_default_registry,
)


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
                           cache_read_input_tokens=7, cache_creation_input_tokens=0)


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


class TestRunLog(unittest.IsolatedAsyncioTestCase):
    async def _recorded_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(AgentConfig(
                role="general", model="mock-model", api_key="k",
                permission_mode="acceptEdits", interactive=False,
            ), registry=build_default_registry())
            runtime.agent._anthropic_client = SimpleNamespace(
                messages=_FakeMessages([
                    _tool_use_stream("read_file", {"file_path": "pkg/a.py"}),
                    _tool_use_stream("edit_file", {"file_path": "pkg/a.py",
                                                   "old_string": "x",
                                                   "new_string": "y"}),
                    _text_stream("done"),
                ]))
            recorder = RunRecorder(task="t-1", agent="general")
            recorder.attach(runtime)
            import os
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                run = await runtime.run("fix something")
            finally:
                os.chdir(prev)
            await runtime.close()
            record = recorder.finish(
                run,
                verification={"passed": True, "tests_passed": 3, "tests_total": 3},
                repair={"attempts": 1, "success": True},
                final_result={"success": True, "tests_total": 3, "tests_passed": 3},
            )
            return record, tmp

    async def test_record_has_all_doc_sections(self):
        record, _ = await self._recorded_run()
        d = record.to_dict()
        # §24 structured events.
        self.assertEqual(record.llm_calls, 3)
        self.assertGreaterEqual(len(d["tool_calls"]), 2)
        self.assertGreaterEqual(len(d["tool_results"]), 2)
        self.assertIsNotNone(d["verification"])
        self.assertIsNotNone(d["repair"])
        self.assertIsNotNone(d["final_result"])
        # §24 metrics.
        m = d["metrics"]
        self.assertGreater(m["input_tokens"], 0)
        self.assertGreater(m["output_tokens"], 0)
        self.assertGreaterEqual(m["cached_tokens"], 0)
        self.assertGreaterEqual(m["tool_calls"], 2)
        self.assertGreaterEqual(m["tool_latency_s"], 0.0)
        self.assertGreaterEqual(m["runtime_s"], 0.0)
        self.assertGreaterEqual(m["estimated_cost_usd"], 0.0)
        self.assertIn("pkg/a.py", m["files_read"])
        self.assertIn("pkg/a.py", m["files_modified"])
        self.assertEqual(m["tests_executed"], 3)
        self.assertEqual(m["repair_attempts"], 1)
        self.assertTrue(m["task_success"])
        self.assertFalse(m["task_failure"])

    async def test_logger_jsonl_roundtrip(self):
        record, tmp = await self._recorded_run()
        logger = RunLogger(Path(tmp) / "runs.jsonl")
        logger.record(record)
        logger.record(record)
        rows = logger.read_all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["run_id"], record.run_id)
        self.assertEqual(rows[1]["metrics"]["task_success"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
