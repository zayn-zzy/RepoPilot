"""run_requirement end-to-end — a scripted five-role team really fixing a
bug inside a real git worktree, real verification, real commit, real PR
body, and real §24 run-log records."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.product import run_requirement  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _make_buggy_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "def multiply(a, b):\n    return a + b\n")  # INJECTED BUG
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_multiply():\n    assert multiply(3, 4) == 12\n")
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


def _payload(role):
    return {
        "planner": {"tasks": [{"id": "T1", "title": "fix multiply",
                               "agent_role": "coder",
                               "description": "make multiply multiply"}],
                    "summary": "fix", "related_files": ["calc.py"]},
        "explorer": {"findings": ["multiply returns a+b"], "related_files": ["calc.py"],
                     "symbols": ["multiply"]},
        "coder": {"files_modified": ["calc.py"], "changes": ["multiply fixed"],
                  "test_commands": ["python -m pytest -q"]},
        "tester": {"passed": True, "failed_tests": [], "summary": "ok"},
        "reviewer": {"approved": True, "issues": [], "suggestions": []},
    }[role]


def _after_build(runtime):
    role = runtime.config.role
    if role == "coder":
        script = [
            _tool_use_stream("read_file", {"file_path": "calc.py"}),
            _tool_use_stream("edit_file", {
                "file_path": "calc.py",
                "old_string": "def multiply(a, b):\n    return a + b",
                "new_string": "def multiply(a, b):\n    return a * b"}),
            _tool_use_stream("publish_artifact", {
                "kind": "code_change", "producer": "coder",
                "payload": _payload("coder")}),
            _text_stream("done"),
        ]
    else:
        script = [
            _tool_use_stream("publish_artifact", {
                "kind": {"planner": "plan", "explorer": "exploration",
                         "tester": "test_report", "reviewer": "review"}[role],
                "producer": role, "payload": _payload(role)}),
            _text_stream("done"),
        ]
    _install_scripted_llm(runtime, script)


class TestRunRequirement(unittest.IsolatedAsyncioTestCase):
    async def test_full_run_on_real_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_buggy_repo(Path(tmp))
            report = await run_requirement(
                repo,
                "multiply() computes a+b instead of a*b — fix the "
                "multiplication bug in calc.py.",
                task_id="T001", model="mock-model", api_key="test-key",
                after_build=_after_build,
            )
            self.assertTrue(report.success, report.note)
            # The worktree ran the real team and the real fix is committed.
            self.assertEqual([o.role for o in report.team_result.outcomes],
                             ["planner", "explorer", "coder", "tester", "reviewer"])
            self.assertTrue(report.verification["passed"])
            self.assertEqual(report.verification["tests_passed"],
                             report.verification["tests_total"])
            # Only the source change enters the patch — verification
            # artifacts (pycache) are purged before diff/commit.
            self.assertEqual(report.diff.all_files, ["calc.py"])
            self.assertTrue(report.commit)
            self.assertIn("return a * b",
                          (report.worktree.path / "calc.py").read_text())
            # The main workspace never changed.
            self.assertIn("return a + b", (repo / "calc.py").read_text())
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            # PR body: the doc's six sections.
            for section in ("Summary", "Changes", "Reason", "Tests",
                            "Risk", "Files Changed"):
                self.assertIn(f"## {section}", report.pr.body)
            self.assertIn("calc.py", report.pr.body)
            self.assertEqual(report.pr.head_branch, "task/T001")
            # §24 run-log: five role records written to .repopilot/runs.jsonl.
            self.assertEqual(len(report.runlog_records), 5)
            log = (repo / ".repopilot" / "runs.jsonl")
            self.assertTrue(log.exists())
            self.assertEqual(len(log.read_text().splitlines()), 5)

    async def test_worktree_failure_is_reported_not_faked(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_git = Path(tmp) / "plain"
            not_git.mkdir()
            report = await run_requirement(not_git, "fix the thing",
                                           task_id="T001")
            self.assertFalse(report.success)
            self.assertIsNone(report.worktree)
            self.assertIn("worktree creation failed", report.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
