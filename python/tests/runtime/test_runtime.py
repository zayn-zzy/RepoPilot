"""AgentRuntime tests — multi-instance independence and LLM mock execution
through the REAL agent loop: only the SDK boundary (anthropic client's
messages.stream) is faked, so streaming, streaming-tool-early-execution,
registry dispatch, ACL enforcement, budget halts, and event emission all run
as production code."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import (  # noqa: E402
    AgentConfig,
    AgentEvents,
    AgentRuntime,
    Tool,
)


# ─── Fake Anthropic SDK boundary ────────────────────────────
# The real Agent._call_anthropic_stream consumes an async-iterator stream of
# events plus a final message. We fake exactly that contract, so everything
# above it (streaming loop, retries, early tool execution) runs for real.


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


def _usage(input_tokens=100, output_tokens=20):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _text_stream(text, **usage_kw):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(text=text)),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=_usage(**usage_kw),
    )
    return _FakeStream(events, final)


def _tool_use_stream(tool_id, name, inp, **usage_kw):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id=tool_id, name=name)),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(partial_json=json.dumps(inp))),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    final = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=tool_id, name=name, input=dict(inp))],
        usage=_usage(**usage_kw),
    )
    return _FakeStream(events, final)


class _FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def stream(self, **params):
        self.calls += 1
        return self._script.pop(0)


class _FakeClient:
    def __init__(self, script):
        self.messages = _FakeMessages(script)


def _make_runtime(role="general", **overrides):
    cfg = AgentConfig.from_role(role, api_key="test-key", interactive=False, **overrides)
    return AgentRuntime(cfg)


def _install_fake_llm(runtime, script):
    runtime.agent._anthropic_client = _FakeClient(script)


class TestRuntimeConstruction(unittest.TestCase):
    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            AgentRuntime(AgentConfig(model="", api_key="k"))

    def test_planner_and_coder_have_their_own_tools(self):
        planner = _make_runtime("planner")
        coder = _make_runtime("coder")
        planner_names = {t["name"] for t in planner.agent.tools}
        coder_names = {t["name"] for t in coder.agent.tools}
        self.assertIn("read_file", planner_names)
        self.assertNotIn("write_file", planner_names)
        self.assertIn("write_file", coder_names)
        self.assertIn("edit_file", coder_names)

    def test_runtimes_are_independent_instances(self):
        a = _make_runtime("explorer")
        b = _make_runtime("explorer")
        self.assertIsNot(a.registry, b.registry)
        self.assertIsNot(a.acl, b.acl)
        self.assertIsNot(a.budget, b.budget)
        self.assertIsNot(a.context, b.context)
        self.assertIsNot(a.agent, b.agent)


class TestRuntimeExecution(unittest.IsolatedAsyncioTestCase):
    async def test_llm_mock_run_with_registry_dispatch(self):
        """Scripted LLM: tool_use(read_file) → text answer. The loop must
        stream, dispatch through the registry, feed the result back, and
        finish with the final text captured."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "data.txt"
            p.write_text("hello\nworld")
            runtime = _make_runtime("explorer")
            _install_fake_llm(runtime, [
                _tool_use_stream("t1", "read_file", {"file_path": str(p)}),
                _text_stream("the file has two lines"),
            ])

            result = await runtime.run("read data.txt")

            self.assertEqual(result.text, "the file has two lines")
            self.assertEqual(result.tokens_in, 200)
            self.assertEqual(result.turns, 1)
            self.assertEqual(runtime.budget.input_tokens, 200)
            # Registry dispatched the built-in through the original executor.
            tool_results = runtime.trace.filter(AgentEvents.TOOL_RESULT)
            self.assertEqual(len(tool_results), 1)
            self.assertIn("hello", tool_results[0].data["result"])
            # Context grew: user + assistant(tool_use) + tool_result + assistant
            self.assertEqual(runtime.context.message_count(), 4)

    async def test_custom_tool_dispatched_through_registry(self):
        """A tool registered after construction is still dispatched by the
        loop — the registry, not the tool list, is the dispatch authority."""
        runtime = _make_runtime("general")
        runtime.registry.register(
            Tool("echo_test", "echo", {"type": "object", "properties": {}},
                 lambda inp: f"echo:{inp.get('text', '')}")
        )
        _install_fake_llm(runtime, [
            _tool_use_stream("t1", "echo_test", {"text": "ping"}),
            _text_stream("done"),
        ])

        result = await runtime.run("echo ping")

        self.assertEqual(result.text, "done")
        tool_result = runtime.trace.filter(AgentEvents.TOOL_RESULT)[0].data
        self.assertEqual(tool_result["result"], "echo:ping")

    async def test_events_emitted_from_loop(self):
        runtime = _make_runtime("general")
        runtime.registry.register(
            Tool("echo_test", "echo", {"type": "object", "properties": {}},
                 lambda inp: "ok")
        )
        _install_fake_llm(runtime, [
            _tool_use_stream("t1", "echo_test", {"text": "x"}),
            _text_stream("done"),
        ])

        await runtime.run("hi")

        self.assertEqual(len(runtime.trace.filter(AgentEvents.RUN_STARTED)), 1)
        self.assertEqual(len(runtime.trace.filter(AgentEvents.RUN_FINISHED)), 1)
        self.assertEqual(len(runtime.trace.filter(AgentEvents.LLM_REQUEST)), 2)
        calls = runtime.trace.filter(AgentEvents.TOOL_CALL)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].data["tool_name"], "echo_test")
        self.assertEqual(calls[0].data["tool_use_id"], "t1")
        metrics = runtime.trace.metrics()
        self.assertEqual(metrics["llm_calls"], 2)
        self.assertEqual(metrics["tool_calls"], 1)

    async def test_read_only_runtime_blocks_write_in_loop(self):
        """acceptEdits lets the write past the mode check, so the role ACL is
        the layer that must deny it — and the file must stay untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secret.txt"
            runtime = _make_runtime("planner", permission_mode="acceptEdits")
            _install_fake_llm(runtime, [
                _tool_use_stream("t1", "write_file",
                                 {"file_path": str(target), "content": "owned"}),
                _text_stream("done"),
            ])

            result = await runtime.run("write it")

            self.assertEqual(result.text, "done")
            self.assertFalse(target.exists())
            tool_result = runtime.trace.filter(AgentEvents.TOOL_RESULT)[0].data
            self.assertIn("read-only", tool_result["result"])
            # Mode check passed (acceptEdits), so no permission_denied event
            # from the loop's static check — the ACL denial arrives as the
            # tool_result string instead.
            denials = runtime.trace.filter(AgentEvents.PERMISSION_DENIED)
            self.assertEqual(len(denials), 0)

    async def test_permission_denied_event_from_plan_mode(self):
        """Plan mode denies run_shell at the static engine → the loop emits
        permission_denied and continues with a refusal tool_result."""
        runtime = _make_runtime("general", permission_mode="plan")
        _install_fake_llm(runtime, [
            _tool_use_stream("t1", "run_shell", {"command": "ls"}),
            _text_stream("done"),
        ])

        result = await runtime.run("list files")

        self.assertEqual(result.text, "done")
        denials = runtime.trace.filter(AgentEvents.PERMISSION_DENIED)
        self.assertEqual(len(denials), 1)
        self.assertEqual(denials[0].data["tool_name"], "run_shell")

    async def test_budget_turn_limit_halts_loop_with_event(self):
        runtime = _make_runtime("general", max_turns=1)
        runtime.registry.register(
            Tool("echo_test", "echo", {"type": "object", "properties": {}}, lambda inp: "ok")
        )
        _install_fake_llm(runtime, [
            _tool_use_stream("t1", "echo_test", {"text": "a"}),
            _text_stream("never reached"),
        ])

        result = await runtime.run("go")

        # The turn limit stops execution before the second LLM call.
        self.assertEqual(runtime.agent._anthropic_client.messages.calls, 1)
        self.assertEqual(result.text, "")
        self.assertEqual(len(runtime.trace.filter(AgentEvents.BUDGET_EXCEEDED)), 1)
        budget_status = runtime.budget.check(runtime.agent.current_turns)
        self.assertTrue(budget_status.exceeded)

    async def test_runs_do_not_leak_state_between_instances(self):
        """A run on one runtime must not touch the other's context, budget,
        trace, or deferred-tool activation."""
        a = _make_runtime("explorer")
        b = _make_runtime("explorer")
        _install_fake_llm(a, [
            _tool_use_stream("t1", "tool_search", {"query": "plan mode"}),
            _text_stream("done"),
        ])

        await a.run("activate deferred")

        self.assertGreater(a.context.message_count(), 0)
        self.assertEqual(b.context.message_count(), 0)
        self.assertEqual(b.budget.input_tokens, 0)
        self.assertEqual(b.trace.events, [])
        # a activated enter_plan_mode; b's registry must not see it.
        a_names = {d["name"] for d in a.registry.active_definitions()}
        b_names = {d["name"] for d in b.registry.active_definitions()}
        self.assertIn("enter_plan_mode", a_names)
        self.assertNotIn("enter_plan_mode", b_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
