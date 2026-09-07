"""TeamRunner ↔ WorktreeManager integration — with a worktree bound in
TeamConfig, the whole team runs inside the task worktree: the coder's
relative file writes land there (never in the main workspace), and the
repo tools (git_diff/git_log/...) read that checkout's git state."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.agents import TeamConfig, TeamRunner, role_acl  # noqa: E402
from mini_claude.agents.roles import build_role_registry  # noqa: E402
from mini_claude.agents.artifact import ArtifactMailbox  # noqa: E402
from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.worktree import WorktreeManager  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout.strip()


def _make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "a.py").write_text("def a():\n    return 1\n")
    (repo / "b.py").write_text("def b():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


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


def _publish_payload(role: str) -> dict:
    return {
        "planner": {"tasks": [{"id": "T1", "title": "implement it",
                               "agent_role": "coder", "description": "write the code"}],
                    "summary": "one task plan", "related_files": ["a.py"]},
        "explorer": {"findings": ["a.py has helpers"], "related_files": ["a.py"],
                     "symbols": ["a"]},
        "coder": {"files_modified": ["feature.py"], "changes": ["added feature"],
                  "test_commands": ["python -m pytest"]},
        "tester": {"passed": True, "failed_tests": [], "summary": "all green"},
        "reviewer": {"approved": True, "issues": [], "suggestions": []},
    }[role]


class TestTeamWorktreeBinding(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        self.mgr = WorktreeManager(self.repo)
        self.wt = self.mgr.create("T001")
        self.idx = RepositoryIndex(self.repo)
        self.idx.build()

    def _scripted_team(self, coder_extra: list | None = None):
        def after_build(runtime):
            role = runtime.config.role
            if role == "coder":
                script = [* (coder_extra or []),
                          _tool_use_stream("publish_artifact", {
                              "kind": "code_change", "producer": "coder",
                              "payload": _publish_payload("coder")}),
                          _text_stream("done")]
            else:
                script = [_tool_use_stream("publish_artifact", {
                    "kind": {"planner": "plan", "explorer": "exploration",
                             "tester": "test_report", "reviewer": "review"}[role],
                    "producer": role, "payload": _publish_payload(role)}),
                    _text_stream("done")]
            _install_scripted_llm(runtime, script)
        return after_build

    async def test_team_bound_to_worktree_writes_inside_it(self):
        """The Phase 5 cwd-pollution fix becomes real isolation: with a
        worktree bound, the coder's relative write lands in the worktree
        and the main workspace never sees it."""
        import os

        write = _tool_use_stream("write_file", {
            "file_path": "feature.py", "content": "def feature():\n    return 42\n"})
        team = TeamRunner(
            TeamConfig(model="m", index=self.idx, api_key="k",
                       permission_mode="acceptEdits", worktree=self.wt),
            after_build=self._scripted_team([write]),
        )
        before = os.getcwd()
        result = await team.run("Add a feature function to the repo")

        # The file exists only in the worktree.
        self.assertTrue((self.wt.path / "feature.py").exists())
        self.assertEqual((self.wt.path / "feature.py").read_text(),
                         "def feature():\n    return 42\n")
        self.assertFalse((self.repo / "feature.py").exists())
        # Main workspace stays clean (worktrees/ excluded) and cwd restored.
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(os.getcwd(), before)
        # The full pipeline still ran to completion.
        self.assertEqual([o.role for o in result.outcomes],
                         ["planner", "explorer", "coder", "tester", "reviewer"])
        self.assertTrue(result.approved)

    async def test_repo_tools_read_worktree_git_state(self):
        """git_diff through the role registry reports the worktree's own
        changes — the coder's diff never mixes in the main workspace."""
        (self.wt.path / "a.py").write_text("def a():\n    return 100\n")
        mailbox = ArtifactMailbox()
        wt_registry = build_role_registry(self.idx, mailbox, git_root=self.wt.path)
        main_registry = build_role_registry(self.idx, mailbox, git_root=self.repo)

        acl = role_acl("coder")
        wt_diff = await wt_registry.dispatch("git_diff", {}, acl=acl)
        main_diff = await main_registry.dispatch("git_diff", {}, acl=acl)
        self.assertIn("return 100", wt_diff)
        self.assertIn("No changes", main_diff)


if __name__ == "__main__":
    unittest.main(verbosity=2)
