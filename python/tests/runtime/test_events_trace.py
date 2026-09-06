"""Event system + Trace tests — subscription, emission, listener isolation,
and metric aggregation."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import AgentEvents, EventEmitter, Trace  # noqa: E402


class TestEmitter(unittest.TestCase):
    def test_on_emit(self):
        em = EventEmitter()
        seen = []

        em.on("evt", lambda d: seen.append(d))
        em.emit("evt", {"a": 1})
        self.assertEqual(seen, [{"a": 1}])

    def test_emit_without_data(self):
        em = EventEmitter()
        seen = []

        em.on("evt", lambda d: seen.append(d))
        em.emit("evt")
        self.assertEqual(seen, [{}])

    def test_off_stops_delivery(self):
        em = EventEmitter()
        seen = []

        def h(d):
            seen.append(d)

        em.on("evt", h)
        em.off("evt", h)
        em.emit("evt", {"a": 1})
        self.assertEqual(seen, [])

    def test_listener_exception_swallowed(self):
        em = EventEmitter()
        seen = []

        def bad(d):
            raise RuntimeError("listener boom")

        def good(d):
            seen.append(d)

        em.on("evt", bad)
        em.on("evt", good)
        em.emit("evt", {"a": 1})  # must not raise
        self.assertEqual(seen, [{"a": 1}])

    def test_listener_count(self):
        em = EventEmitter()
        em.on("a", lambda d: None)
        em.on("a", lambda d: None)
        em.on("b", lambda d: None)
        self.assertEqual(em.listener_count("a"), 2)
        self.assertEqual(em.listener_count(), 3)


class TestTrace(unittest.TestCase):
    def test_attach_records_events(self):
        em = EventEmitter()
        trace = Trace().attach(em)
        em.emit(AgentEvents.RUN_STARTED, {"run_id": "r1"})
        em.emit(AgentEvents.TOOL_CALL, {"tool_name": "read_file"})
        em.emit(AgentEvents.TOOL_CALL, {"tool_name": "grep_search"})
        em.emit(AgentEvents.RUN_FINISHED, {"run_id": "r1"})

        self.assertEqual(len(trace.events), 4)
        self.assertEqual([e.data["tool_name"] for e in trace.filter(AgentEvents.TOOL_CALL)],
                         ["read_file", "grep_search"])
        for e in trace.events:
            self.assertGreater(e.ts_epoch, 0)
            self.assertGreaterEqual(e.ts_monotonic, 0)

    def test_metrics(self):
        em = EventEmitter()
        trace = Trace().attach(em)
        em.emit(AgentEvents.RUN_STARTED, {"run_id": "r"})
        em.emit(AgentEvents.LLM_REQUEST, {})
        em.emit(AgentEvents.LLM_REQUEST, {})
        em.emit(AgentEvents.TOOL_CALL, {})
        em.emit(AgentEvents.TOOL_RESULT, {})
        em.emit(AgentEvents.PERMISSION_DENIED, {})
        em.emit(AgentEvents.RUN_FINISHED, {
            "tokens_in": 100, "tokens_out": 50, "turns": 2, "cost_usd": 0.001,
        })

        m = trace.metrics()
        self.assertEqual(m["llm_calls"], 2)
        self.assertEqual(m["tool_calls"], 1)
        self.assertEqual(m["tool_results"], 1)
        self.assertEqual(m["permission_denials"], 1)
        self.assertEqual(m["input_tokens"], 100)
        self.assertEqual(m["output_tokens"], 50)
        self.assertEqual(m["turns"], 2)
        self.assertAlmostEqual(m["cost_usd"], 0.001)
        self.assertIsNotNone(m["runtime_s"])

    def test_untyped_events_ignored(self):
        """The trace only records the canonical AgentEvents types."""
        em = EventEmitter()
        trace = Trace().attach(em)
        em.emit("something_else", {"a": 1})
        self.assertEqual(trace.events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
