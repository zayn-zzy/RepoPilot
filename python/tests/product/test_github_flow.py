"""github_flow tests — the Phase 17 productized flow: push (real git,
real bare remote), PR creation/merge (a fake `gh` script recording its
argv), and cleanup (real worktrees, the never-by-force safety rules).
The E2E runs a real DagRunner with a scripted LLM (the same contract as
the team/runner tests) and drives the whole push → PR → merge → cleanup
flow on it; only `gh` is fake (this machine has no gh)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.product.github import GhError  # noqa: E402
from mini_claude.product.github_flow import (  # noqa: E402
    merge_pr,
    parse_remote_repo,
    pr_number_from_url,
    push_branch,
    run_github_flow,
)
from mini_claude.planning import TaskDAG  # noqa: E402
from mini_claude.planning.task import TaskNode  # noqa: E402
from mini_claude.worktree import WorktreeManager  # noqa: E402

CALC = ("def add(a, b):\n    return a + b\n\n\n"
        "def multiply(a, b):\n    return a + b\n")
FIXED_CALC = ("def add(a, b):\n    return a + b\n\n\n"
              "def multiply(a, b):\n    return a * b\n")


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _make_repo(tmp: Path, *, with_origin: bool = False) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "calc.py").write_text(CALC)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_multiply():\n    assert multiply(3, 4) == 12\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    if with_origin:
        origin = tmp / "origin.git"
        origin.mkdir()
        _git(origin, "init", "--bare", "-q")
        _git(repo, "remote", "add", "origin", str(origin))
    return repo


def _make_fake_gh(tmp: Path) -> Path:
    """A `gh` stand-in: logs its argv and answers the two calls the flow
    makes (pr create → URL; pr merge → merged)."""
    log = tmp / "gh.log"
    script = tmp / "gh"
    script.write_text(
        "#!/bin/bash\n"
        f"echo \"$@\" >> {log}\n"
        "case \"$1\" in\n"
        "  --version) exit 0 ;;\n"
        "  pr)\n"
        "    case \"$2\" in\n"
        "      create) echo \"https://github.com/o/r/pull/42\" ;;\n"
        "      merge) echo \"merged\" ;;\n"
        "      *) exit 1 ;;\n"
        "    esac ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n")
    script.chmod(0o755)
    return script


def _gh_log(tmp: Path) -> list[str]:
    log = tmp / "gh.log"
    return log.read_text().splitlines() if log.exists() else []


# ─── scripted LLM (same contract as the team/runner tests) ─────

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


def _coder_script(fixed: str) -> list:
    return [_tool_use_stream("read_file", {"file_path": "calc.py"}),
            _tool_use_stream("write_file", {"file_path": "calc.py",
                                            "content": fixed}),
            _text_stream("done")]


# ─── unit parts ───────────────────────────────────────────────

class TestRemoteAndPush(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name), with_origin=True)

    def test_parse_remote_repo_url_forms(self):
        for url, expected in (
            ("https://github.com/owner/repo.git", "owner/repo"),
            ("git@github.com:owner/repo.git", "owner/repo"),
            ("ssh://git@github.com/owner/repo.git", "owner/repo"),
            ("https://github.com/owner/repo", "owner/repo"),
        ):
            _git(self.repo, "remote", "set-url", "origin", url)
            self.assertEqual(parse_remote_repo(self.repo), expected, url)

    def test_parse_remote_repo_without_origin_is_explicit(self):
        _git(self.repo, "remote", "remove", "origin")
        with self.assertRaises(GhError) as ctx:
            parse_remote_repo(self.repo)
        self.assertIn("no 'origin' remote", str(ctx.exception))

    def test_push_branch_reaches_the_remote(self):
        out = push_branch(self.repo, "main")
        self.assertIn("main", out)
        origin = Path(self._tmp.name) / "origin.git"
        listed = subprocess.run(
            ["git", "--git-dir", str(origin), "branch", "--list"],
            capture_output=True, text=True).stdout
        self.assertIn("main", listed)

    def test_pr_number_from_url(self):
        self.assertEqual(pr_number_from_url("https://github.com/o/r/pull/42"), 42)
        self.assertIsNone(pr_number_from_url("https://github.com/o/r"))

    def test_merge_pr_argv_and_honest_failure(self):
        gh = _make_fake_gh(Path(self._tmp.name))
        out = merge_pr("o/r", 42, method="squash", delete_branch=True, gh_bin=str(gh))
        self.assertIn("merged", out)
        log = _gh_log(Path(self._tmp.name))
        self.assertTrue(any("pr merge 42 --repo o/r --squash --delete-branch" in line
                            for line in log), log)
        with self.assertRaises(GhError):
            merge_pr("o/r", 42, gh_bin="/bin/false")


# ─── the flow itself ──────────────────────────────────────────

def _namespace_report(*, success: bool, worktree=None, pr=None) -> SimpleNamespace:
    return SimpleNamespace(success=success, worktree=worktree, pr=pr)


class TestGithubFlow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_failed_run_is_never_pushed(self):
        repo = _make_repo(Path(self._tmp.name))
        report = _namespace_report(success=False,
                                   worktree=SimpleNamespace(branch="task/T1"))
        result = run_github_flow(repo, report, push=True, pr=True)
        self.assertFalse(result.pushed)
        self.assertIn("did not succeed", result.push_note)

    def test_no_origin_is_explicit(self):
        repo = _make_repo(Path(self._tmp.name))  # no remote
        report = _namespace_report(
            success=True, worktree=SimpleNamespace(branch="task/T1"),
            pr=SimpleNamespace(title="t", body="b", head_branch="task/T1",
                               base_branch="main"))
        result = run_github_flow(repo, report, push=True, pr=True)
        self.assertFalse(result.pushed)
        self.assertIn("no 'origin' remote", result.push_note)

    def test_e2e_run_push_pr_merge_cleanup(self):
        """The full productized flow on a real run: scripted LLM fixes
        multiply(), verification passes for real, the branch is pushed to
        a real bare remote, a fake `gh` creates and merges the PR, and
        cleanup removes the worktrees whose work is preserved."""
        tmp = Path(self._tmp.name)
        repo = _make_repo(tmp, with_origin=True)
        gh = _make_fake_gh(tmp)
        from mini_claude.planning import RequirementParser
        from mini_claude.product.orchestrator import run_dag_requirement

        dag = TaskDAG([TaskNode(id="T001", title="fix multiply",
                                description="multiply must return a*b",
                                agent_role="coder")])

        import asyncio

        def after_build(runtime, task):
            _install_scripted_llm(runtime, _coder_script(FIXED_CALC))

        report = asyncio.run(run_dag_requirement(
            repo, RequirementParser().parse("fix the multiply bug"),
            plan=dag, task_id="RUN1", model="mock-model", api_key="test-key",
            jobs=1, sandbox="off", after_build=after_build,
            max_repair_attempts=1))
        self.assertTrue(report.success, report.summarize())

        result = run_github_flow(
            repo, report, push=True, pr=True, merge=True, cleanup=True,
            gh_bin=str(gh), manager=WorktreeManager(repo))
        print(result.summarize())

        # push: the branch is on the real bare remote
        origin = tmp / "origin.git"
        listed = subprocess.run(
            ["git", "--git-dir", str(origin), "branch", "--list"],
            capture_output=True, text=True).stdout
        self.assertIn(report.worktree.branch, listed)
        # PR + merge via fake gh, with the right argv
        self.assertEqual(result.pr_url, "https://github.com/o/r/pull/42")
        self.assertEqual(result.pr_number, 42)
        self.assertTrue(result.merged)
        log = _gh_log(tmp)
        self.assertTrue(any("pr create --repo" in entry and
                            f"--head {report.worktree.branch}" in entry and
                            "--base main" in entry for entry in log), log)
        self.assertTrue(any("pr merge 42 --repo" in entry and "--merge" in entry
                            and "--delete-branch" in entry
                            for entry in log), log)
        # cleanup: worktrees and their branches are gone, work preserved
        self.assertEqual(sorted(result.cleaned),
                         sorted([report.worktree.task_id, "RUN1-T001"]))
        self.assertEqual(result.cleanup_refused, [])
        self.assertFalse((repo / "worktrees").exists() and
                         any((repo / "worktrees").iterdir()))
        self.assertEqual(_git(repo, "branch", "--list", "task/*"), "")
        # the merged result is in the remote; the main workspace untouched
        self.assertIn("return a * b",
                      subprocess.run(["git", "--git-dir", str(origin), "show",
                                      f"{report.worktree.branch}:calc.py"],
                                     capture_output=True, text=True).stdout)
        self.assertEqual((repo / "calc.py").read_text(), CALC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
