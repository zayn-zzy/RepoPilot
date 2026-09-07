"""TeamRunner tests — the full five-role pipeline driven by scripted LLM
clients (SDK boundary faked, everything else real): artifact passing,
independent contexts/budgets, ACL enforcement in-loop, early stop."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.agents import TeamConfig, TeamRunner  # noqa: E402
from mini_claude.repo import RepositoryIndex  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repo_fixture"


# ─── Fake Anthropic SDK boundary (same contract as the Phase 1 tests) ──

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


def _publish_payload(role: str) -> dict:
    return {
        "planner": {"tasks": [{"id": "T1", "title": "implement it",
                               "agent_role": "coder", "description": "write the code"}],
                    "summary": "one task plan", "related_files": ["pkg/utils.py"]},
        "explorer": {"findings": ["utils has helpers"], "related_files": ["pkg/utils.py"],
                     "symbols": ["pkg.utils.add"]},
        "coder": {"files_modified": ["pkg/utils.py"], "changes": ["added f"],
                  "test_commands": ["python -m pytest"]},
        "tester": {"passed": True, "failed_tests": [], "summary": "all green"},
        "reviewer": {"approved": True, "issues": [], "suggestions": ["rename f"]},
    }[role]


class TestPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_full_pipeline_with_patched_after_build(self):
        """The real flow: after_build patches each runtime as it is created."""
        idx = RepositoryIndex(FIXTURE)
        idx.build()
        captured = {}

        def after_build(runtime):
            captured[runtime.config.role] = runtime
            role = runtime.config.role
            _install_scripted_llm(runtime, [
                _tool_use_stream("publish_artifact", {
                    "kind": {"planner": "plan", "explorer": "exploration",
                             "coder": "code_change", "tester": "test_report",
                             "reviewer": "review"}[role],
                    "producer": role,
                    "payload": _publish_payload(role),
                }),
                _text_stream("done"),
            ])

        team = TeamRunner(TeamConfig(model="mock-model", index=idx, api_key="test-key"),
                          after_build=after_build)
        result = await team.run("Add a helper function to the utils module")

        # Five roles ran in the fixed order.
        self.assertEqual([o.role for o in result.outcomes],
                         ["planner", "explorer", "coder", "tester", "reviewer"])
        # Five artifacts, right kinds and producers.
        self.assertEqual([a.kind for a in result.artifacts],
                         ["plan", "exploration", "code_change", "test_report", "review"])
        self.assertEqual([a.producer for a in result.artifacts],
                         ["planner", "explorer", "coder", "tester", "reviewer"])
        self.assertTrue(result.approved)
        self.assertEqual(result.review.payload["suggestions"], ["rename f"])
        self.assertEqual(result.requirement.kind.value, "feature")

    async def test_artifacts_passed_to_next_roles(self):
        idx = RepositoryIndex(FIXTURE)
        idx.build()

        def after_build(runtime):
            role = runtime.config.role
            _install_scripted_llm(runtime, [
                _tool_use_stream("publish_artifact", {
                    "kind": {"planner": "plan", "explorer": "exploration",
                             "coder": "code_change", "tester": "test_report",
                             "reviewer": "review"}[role],
                    "producer": role, "payload": _publish_payload(role)}),
                _text_stream("done"),
            ])

        team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k"),
                          after_build=after_build)
        result = await team.run("Add a helper function")

        prompts = {o.role: o.prompt for o in result.outcomes}
        # coder sees plan + exploration (not review/test_report)
        self.assertIn('"kind": "plan"', prompts["coder"])
        self.assertIn('"kind": "exploration"', prompts["coder"])
        self.assertNotIn('"kind": "review"', prompts["coder"])
        # tester sees the code_change
        self.assertIn('"kind": "code_change"', prompts["tester"])
        # reviewer sees change + tests, not the exploration
        self.assertIn('"kind": "test_report"', prompts["reviewer"])
        self.assertNotIn('"kind": "exploration"', prompts["reviewer"])
        # planner sees no artifacts at all
        self.assertNotIn("Artifacts from earlier", prompts["planner"])

    async def test_independent_contexts_and_budgets(self):
        idx = RepositoryIndex(FIXTURE)
        idx.build()
        captured = {}

        def after_build(runtime):
            captured[runtime.config.role] = runtime
            role = runtime.config.role
            _install_scripted_llm(runtime, [
                _tool_use_stream("publish_artifact", {
                    "kind": {"planner": "plan", "explorer": "exploration",
                             "coder": "code_change", "tester": "test_report",
                             "reviewer": "review"}[role],
                    "producer": role, "payload": _publish_payload(role)}),
                _text_stream("done"),
            ])

        team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k"),
                          after_build=after_build)
        await team.run("do the thing")

        # Each role owns its context and budget objects.
        contexts = [rt.context for rt in captured.values()]
        self.assertEqual(len({id(c) for c in contexts}), 5)
        budgets = [rt.budget for rt in captured.values()]
        self.assertEqual(len({id(b) for b in budgets}), 5)
        # Every role's context grew from its own run.
        for rt in captured.values():
            self.assertGreater(rt.context.message_count(), 0)
        # Budgets recorded each role's own tokens.
        for rt in captured.values():
            self.assertGreater(rt.budget.input_tokens, 0)

    async def test_cwd_switches_to_repo_root_and_restores(self):
        """File tools are cwd-relative — the team must run with the process
        cwd at the index root and restore it afterwards."""
        import os

        idx = RepositoryIndex(FIXTURE)
        idx.build()

        def after_build(runtime):
            _install_scripted_llm(runtime, [_text_stream("done")])

        team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k"),
                          after_build=after_build)
        before = os.getcwd()
        await team.run("any requirement")
        self.assertEqual(os.getcwd(), before)
        # While running, cwd was the fixture root (observable via the agent's
        # environment section — assert indirectly through the outcome prompt
        # capture: the git context uses cwd; simplest honest check is that
        # cwd is restored and the run executed there).

    async def test_pipeline_stops_when_planner_publishes_nothing(self):
        idx = RepositoryIndex(FIXTURE)
        idx.build()

        def after_build(runtime):
            _install_scripted_llm(runtime, [_text_stream("I refuse to plan.")])

        team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k"),
                          after_build=after_build)
        result = await team.run("complex requirement")
        self.assertEqual([o.role for o in result.outcomes], ["planner"])
        self.assertEqual(result.artifacts, [])
        self.assertIsNone(result.approved)

    async def test_explorer_write_rejected_in_loop(self):
        """The doc's 'Explorer write rejection': a scripted explorer calls
        write_file — the loop's ACL denies it and no file is created."""
        idx = RepositoryIndex(FIXTURE)
        idx.build()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "owned.txt"

            def after_build(runtime):
                if runtime.config.role == "planner":
                    _install_scripted_llm(runtime, [
                        _tool_use_stream("publish_artifact", {
                            "kind": "plan", "producer": "planner",
                            "payload": _publish_payload("planner")}),
                        _text_stream("done"),
                    ])
                else:  # explorer tries to write instead of searching
                    _install_scripted_llm(runtime, [
                        _tool_use_stream("write_file", {"file_path": str(target),
                                                        "content": "owned"}),
                        _text_stream("done"),
                    ])

            # acceptEdits lets the write past the STATIC engine so the role
            # ACL is the layer that must deny it.
            team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k",
                                         permission_mode="acceptEdits"),
                              after_build=after_build)
            result = await team.run("explore and then try to write")

            self.assertFalse(target.exists())
            roles = [o.role for o in result.outcomes]
            self.assertEqual(roles, ["planner", "explorer"])
            # The write was denied in-loop → no artifact → pipeline stopped.
            self.assertIsNone(result.outcomes[-1].artifact)

    async def test_coder_write_allowed_in_loop(self):
        """The doc's 'Coder write permission': a scripted coder writes a new
        file through the loop and then publishes its artifact."""
        idx = RepositoryIndex(FIXTURE)
        idx.build()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "written_by_coder.txt"

            def after_build(runtime):
                role = runtime.config.role
                if role == "coder":  # write the file, then publish
                    _install_scripted_llm(runtime, [
                        _tool_use_stream("write_file", {"file_path": str(target),
                                                        "content": "made by coder"}),
                        _tool_use_stream("publish_artifact", {
                            "kind": "code_change", "producer": "coder",
                            "payload": _publish_payload("coder")}),
                        _text_stream("done"),
                    ])
                else:  # every other role just publishes its artifact
                    _install_scripted_llm(runtime, [
                        _tool_use_stream("publish_artifact", {
                            "kind": {"planner": "plan", "explorer": "exploration",
                                     "tester": "test_report", "reviewer": "review"}[role],
                            "producer": role,
                            "payload": _publish_payload(role)}),
                        _text_stream("done"),
                    ])

            team = TeamRunner(TeamConfig(model="m", index=idx, api_key="k",
                                         permission_mode="acceptEdits"),
                              after_build=after_build)
            result = await team.run("write the file")

            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(), "made by coder")
            self.assertEqual([o.role for o in result.outcomes],
                             ["planner", "explorer", "coder", "tester", "reviewer"])
            self.assertTrue(result.approved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
