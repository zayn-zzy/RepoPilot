"""GitHub flow tests — issue mapping, the six-section PR body, and the
explicit failures when `gh` is unavailable."""

import sys
import unittest
from pathlib import Path
from unittest import mock

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import RequirementParser  # noqa: E402
from mini_claude.product import (  # noqa: E402
    PR_SECTIONS,
    build_pr_body,
    create_pull_request,
    fetch_issue,
    issue_to_requirement,
)
from mini_claude.product.github import GhError  # noqa: E402
from mini_claude.worktree import TaskDiff  # noqa: E402


def _diff():
    return TaskDiff(task_id="T1", branch="task/T1", base_commit="abc",
                    head_commit="def", files=["pkg/utils.py"],
                    untracked=["pkg/new.py"],
                    patch="- return a + b\n+ return a * b\n",
                    stat=" pkg/utils.py | 2 +-")


class _Team:
    def __init__(self, approved=True):
        self.approved = approved
        self.review = SimpleReview(approved)


class SimpleReview:
    def __init__(self, approved):
        self.payload = ({"approved": approved, "issues": ["edge case x"],
                         "suggestions": ["rename helper"]})


class TestIssueFlow(unittest.TestCase):
    def test_issue_to_requirement(self):
        req = issue_to_requirement({
            "title": "Fix discount math",
            "body": "A 20% discount on 100 returns 20 instead of 80. "
                    "Fix the calculation in pricing.py.",
        })
        self.assertEqual(req.title, "Fix discount math")
        self.assertIn("discount", req.description.lower())
        self.assertIn("pricing.py", req.related_files)

    def test_untitled_issue_gets_title(self):
        req = issue_to_requirement({"title": "", "body": "do the thing"})
        self.assertEqual(req.title, "(untitled issue)")

    def test_pr_body_has_all_six_sections(self):
        req = RequirementParser().parse(
            "Fix discount math. pricing.py computes discounts wrong.")
        body = build_pr_body(requirement=req, diff=_diff(),
                             team_result=_Team(approved=True),
                             verification={"passed": True, "tests_passed": 7,
                                           "tests_total": 7},
                             repair={"attempts": 1, "success": True},
                             base_branch="main")
        for section in PR_SECTIONS:
            self.assertIn(f"## {section}", body, section)
        self.assertIn("pkg/utils.py", body)
        self.assertIn("pkg/new.py` (untracked)", body)
        self.assertIn("PASS", body)
        self.assertIn("Self-repair used: 1 attempt(s), success=True", body)
        self.assertIn("Reviewer approved", body)
        self.assertIn("task/T1", body)

    def test_pr_body_without_reviewer_is_honest(self):
        req = RequirementParser().parse("fix thing")
        body = build_pr_body(requirement=req, diff=_diff(),
                             team_result=None, verification=None,
                             repair=None)
        self.assertIn("No reviewer verdict", body)
        # No verification/repair data was passed — nothing claims to have it.
        self.assertNotIn("Verification: PASS", body)
        self.assertNotIn("Self-repair used", body)
        self.assertIn("Repository's own test suite run", body)

    def test_fetch_issue_without_gh_is_explicit(self):
        with mock.patch("mini_claude.product.github._gh_available",
                        return_value=False):
            with self.assertRaises(GhError) as ctx:
                fetch_issue("owner/repo", 7)
            self.assertIn("gh", ctx.exception.args[0])

    def test_fetch_issue_with_gh_returns_json(self):
        with mock.patch("mini_claude.product.github._gh_available",
                        return_value=True), \
             mock.patch("mini_claude.product.github.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0,
                                         stdout='{"title": "t", "body": "b"}')
            issue = fetch_issue("owner/repo", 7)
        self.assertEqual(issue["title"], "t")
        run.assert_called_once()

    def test_create_pr_without_gh_gives_ready_command(self):
        from mini_claude.product.github import PrInfo
        info = PrInfo(title="t", body="b", head_branch="task/T1")
        with mock.patch("mini_claude.product.github._gh_available",
                        return_value=False):
            with self.assertRaises(GhError) as ctx:
                create_pull_request(info, "owner/repo")
        self.assertIn("gh pr create", ctx.exception.args[0])
        self.assertIn("task/T1", ctx.exception.args[0])

    def test_create_pr_with_gh_returns_url(self):
        from mini_claude.product.github import PrInfo
        with mock.patch("mini_claude.product.github._gh_available",
                        return_value=True), \
             mock.patch("mini_claude.product.github.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0,
                                         stdout="https://github.com/o/r/pull/9")
            url = create_pull_request(PrInfo(title="t", body="b",
                                             head_branch="task/T1"),
                                      "owner/repo")
        self.assertEqual(url, "https://github.com/o/r/pull/9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
