"""GitHub issue → PR flow (doc Phase 10).

    GitHub Issue → Requirement → RepoPilot Run → Patch → Commit
    → PR Description Generation → Pull Request

Only the requirement mapping and the PR-description generation are
pure; everything that talks to GitHub goes through the `gh` CLI. When
`gh` is unavailable the failure is explicit and actionable (never a
fabricated result).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from ..planning import Requirement, RequirementParser
from ..worktree import TaskDiff

# File mentions in issue prose ("in pricing.py", "see src/cli/main.py"):
# the Phase 4 parser only extracts files from stack traces / FAILED lines,
# so the issue flow adds this prose-level extraction on top.
_FILE_MENTION_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|jsx|tsx|md|json|toml|ya?ml|sh|rs|go|java)\b")

# The doc's minimum PR content — every section is always present.
PR_SECTIONS = ("Summary", "Changes", "Reason", "Tests", "Risk",
               "Files Changed")


@dataclass
class PrInfo:
    title: str
    body: str
    head_branch: str
    base_branch: str = "main"


def issue_to_requirement(issue: dict) -> Requirement:
    """GitHub issue JSON (title + body) → the Phase 4 Requirement.

    On top of the Phase 4 parser (kinds, stack traces, FAILED lines),
    file mentions in the prose are added to related_files — issues
    routinely name the files they are about."""
    parser = RequirementParser()
    title = str(issue.get("title", "")).strip() or "(untitled issue)"
    body = str(issue.get("body", "")).strip()
    text = f"{title}\n{body}"
    req = parser.parse(text)
    if not req.title:
        req.title = title
    mentioned = [m.group(0) for m in _FILE_MENTION_RE.finditer(body)]
    req.related_files = list(dict.fromkeys(req.related_files + mentioned))
    return req


def build_pr_body(*, requirement: Requirement, diff: TaskDiff,
                  team_result=None, verification: dict | None = None,
                  repair: dict | None = None,
                  base_branch: str = "main") -> str:
    """The PR description with the doc's six sections, filled from the
    real run artifacts (task diff, team review, verification/repair)."""
    review = team_result.review.payload if (team_result is not None
                                            and team_result.review is not None) else {}
    approved = team_result.approved if team_result is not None else None

    changes = []
    for f in diff.all_files:
        note = " (untracked)" if f in diff.untracked else ""
        changes.append(f"- `{f}`{note}")

    tests_section = ["- Repository's own test suite run by the verification "
                     "pipeline."]
    if verification:
        ok = verification.get("passed")
        tests_section.append(
            f"- Verification: {'PASS' if ok else 'FAIL'} "
            f"({verification.get('tests_passed', 0)}/{verification.get('tests_total', 0)} "
            f"tests passed)")
    if repair and repair.get("attempts"):
        tests_section.append(
            f"- Self-repair used: {repair['attempts']} attempt(s), "
            f"success={repair.get('success')}")

    risk = []
    if approved is True:
        risk.append("- Reviewer approved the change; issues: "
                    + (", ".join(review.get("issues", [])) or "none") + ".")
    elif approved is False:
        risk.append("- Reviewer did NOT approve: "
                    + (", ".join(review.get("issues", [])) or "no reasons given") + ".")
    else:
        risk.append("- No reviewer verdict (pipeline did not reach review).")
    for s in review.get("suggestions", []):
        risk.append(f"- Suggestion: {s}")

    return "\n\n".join([
        "## Summary",
        requirement.title,
        "## Changes",
        "\n".join(changes) or "- (no file changes recorded)",
        "## Reason",
        requirement.description or "(no description provided)",
        "## Tests",
        "\n".join(tests_section),
        "## Risk",
        "\n".join(risk),
        "## Files Changed",
        f"Base branch: `{base_branch}`  |  Task branch: `{diff.branch}`  |  "
        f"Files: {len(diff.all_files)}",
    ])


class GhError(RuntimeError):
    """GitHub integration is unavailable — the message says exactly why."""


def fetch_issue(repo: str, number: int) -> dict:
    """Fetch one GitHub issue via `gh` (JSON). Raises GhError with a
    precise reason when gh is missing or the fetch fails."""
    if not _gh_available():
        raise GhError(
            "the GitHub CLI (gh) is not installed or not authenticated — "
            "install it and run `gh auth login`, or pass the issue JSON "
            "directly to issue_to_requirement()")
    p = subprocess.run(
        ["gh", "issue", "view", str(number), "--repo", repo, "--json",
         "title,body,number,state"],
        capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise GhError(f"gh issue view failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


def create_pull_request(info: PrInfo, repo: str, *,
                        labels: list[str] | None = None) -> str:
    """Create the PR via `gh`. Returns the PR URL on success; raises
    GhError otherwise."""
    if not _gh_available():
        raise GhError(
            "the GitHub CLI (gh) is not installed or not authenticated — "
            f"the PR body is ready; create it manually with:\n"
            f"  gh pr create --repo {repo} --head {info.head_branch} "
            f"--base {info.base_branch} --title \"{info.title}\" --body-file <file>")
    argv = ["gh", "pr", "create", "--repo", repo,
            "--head", info.head_branch, "--base", info.base_branch,
            "--title", info.title, "--body", info.body]
    for label in labels or []:
        argv += ["--label", label]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise GhError(f"gh pr create failed: {p.stderr.strip()}")
    return p.stdout.strip()


def _gh_available() -> bool:
    p = subprocess.run(["gh", "--version"], capture_output=True, text=True,
                       timeout=15)
    return p.returncode == 0
