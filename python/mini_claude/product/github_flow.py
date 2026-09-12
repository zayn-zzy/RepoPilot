"""github_flow — the productized GitHub flow (Phase 17).

    GitHub Issue → Requirement → Run → Patch → Commit
    → Push → Pull Request → (optional) Merge → (optional) Cleanup

The `run` / `issue` commands call this after the work is done. The push
runs through git plumbing; everything that talks to GitHub runs through
the `gh` CLI (injectable for tests). Every step either succeeds for real
or fails with the exact reason — nothing is ever fabricated, and a
failed step skips its downstream steps with an honest note. Cleanup
never discards work: a worktree is removed only when its commits are
merged into the integration branch or preserved on the remote.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .github import GhError, create_pull_request

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


@dataclass
class GithubResult:
    """What the GitHub flow did, step by step — always the truth."""

    repo: str = ""
    pushed: bool = False
    push_note: str = ""
    pr_url: str = ""
    pr_number: int | None = None
    pr_note: str = ""
    merged: bool = False
    merge_note: str = ""
    cleaned: list[str] = field(default_factory=list)
    cleanup_refused: list[str] = field(default_factory=list)
    cleanup_skipped: str = ""

    def summarize(self) -> str:
        lines = ["github:"]
        if self.repo:
            lines.append(f"  repo: {self.repo}")
        if self.push_note:
            lines.append(f"  push: {'ok' if self.pushed else 'FAILED'} — "
                         f"{self.push_note}")
        if self.pr_url:
            lines.append(f"  PR: {self.pr_url} (#{self.pr_number})")
        elif self.pr_note:
            lines.append(f"  PR: not created — {self.pr_note}")
        if self.merge_note:
            lines.append(f"  merge: {'ok' if self.merged else 'FAILED'} — "
                         f"{self.merge_note}")
        if self.cleaned:
            lines.append(f"  cleanup: removed worktree(s) "
                         f"{', '.join(self.cleaned)}")
        for refusal in self.cleanup_refused:
            lines.append(f"  cleanup refused: {refusal}")
        if self.cleanup_skipped:
            lines.append(f"  cleanup: {self.cleanup_skipped}")
        return "\n".join(lines)


# ─── git plumbing (real, no network tricks) ──────────────────

def parse_remote_repo(root: str | Path) -> str:
    """owner/repo of the origin remote. Raises GhError with the exact
    reason when there is none or the URL shape is not recognized."""
    p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(root),
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0 or not p.stdout.strip():
        raise GhError("no 'origin' remote is configured — pushing/PRs need "
                      "one (git remote add origin <url>)")
    url = p.stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url.rstrip("/"))
    if m is None:
        raise GhError(f"cannot parse owner/repo from origin URL {url!r}")
    return f"{m.group(1)}/{m.group(2)}"


def push_branch(root: str | Path, branch: str) -> str:
    """`git push -u origin <branch>`. Returns stdout; raises GhError
    with the exact stderr on failure (no remote, no auth, no network)."""
    p = subprocess.run(["git", "push", "-u", "origin", branch], cwd=str(root),
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise GhError(f"git push origin {branch} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def merge_pr(repo: str, number: int, *, method: str = "merge",
             delete_branch: bool = False, gh_bin: str | None = None) -> str:
    """`gh pr merge <number>` (--merge/--squash/--rebase). Raises GhError
    with the exact reason on failure."""
    gh = gh_bin or "gh"
    argv = [gh, "pr", "merge", str(number), "--repo", repo, f"--{method}"]
    if delete_branch:
        argv.append("--delete-branch")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except OSError as e:
        raise GhError(f"gh could not be executed: {e}") from e
    if p.returncode != 0:
        raise GhError(f"gh pr merge failed: {p.stderr.strip()}")
    return p.stdout.strip()


def pr_number_from_url(url: str) -> int | None:
    m = _PR_NUMBER_RE.search(url)
    return int(m.group(1)) if m else None


# ─── the flow ────────────────────────────────────────────────

def run_github_flow(root: str | Path, report, *,
                    push: bool = True,
                    pr: bool = True,
                    merge: bool = False,
                    cleanup: bool = False,
                    merge_method: str = "merge",
                    gh_bin: str | None = None,
                    manager=None) -> GithubResult:
    """The post-run GitHub flow. ``report`` is a DagRunReport/RunReport
    (needs .success, .worktree, .pr). A run that did not succeed is
    never pushed (broken code must not leave the machine); each step
    runs only when its predecessor succeeded, and every skip says why."""
    root = Path(root).resolve()
    result = GithubResult()
    if report.worktree is None:
        result.cleanup_skipped = "no worktree — nothing to do"
        result.push_note = result.cleanup_skipped
        return result
    branch = report.worktree.branch

    if not report.success:
        result.cleanup_skipped = ("run did not succeed — push/PR/merge skipped "
                                  "(nothing productized from a broken run)")
        result.push_note = result.cleanup_skipped
        # Cleanup may still run: its safety checks decide what is kept.
        if cleanup and manager is not None:
            _cleanup_worktrees(manager, report, result, pushed=False)
        return result

    try:
        result.repo = parse_remote_repo(root)
    except GhError as e:
        result.push_note = str(e)
        if cleanup and manager is not None:
            _cleanup_worktrees(manager, report, result, pushed=False)
        return result

    if push or pr or merge:
        try:
            result.push_note = push_branch(root, branch)
            result.pushed = True
        except GhError as e:
            result.push_note = str(e)
            if cleanup and manager is not None:
                _cleanup_worktrees(manager, report, result, pushed=False)
            return result
    else:
        result.push_note = "skipped (--push/--pr/--merge not requested)"

    if pr and report.pr is not None:
        try:
            url = create_pull_request(report.pr, result.repo, gh_bin=gh_bin)
            result.pr_url = url.strip()
            result.pr_number = pr_number_from_url(url)
        except GhError as e:
            result.pr_note = str(e)

    if merge and result.pr_number is not None:
        try:
            result.merge_note = merge_pr(
                result.repo, result.pr_number, method=merge_method,
                delete_branch=cleanup, gh_bin=gh_bin)
            result.merged = True
        except GhError as e:
            result.merge_note = str(e)

    if cleanup and manager is not None:
        _cleanup_worktrees(manager, report, result, pushed=result.pushed)
    return result


def _cleanup_worktrees(manager, report, result: GithubResult, *,
                       pushed: bool) -> None:
    """Remove worktrees whose work is preserved — never by force:

    - per-task worktrees: removed when their branch is fully merged into
      the integration branch (a failed task's unmerged work is kept);
    - the integration worktree: removed only when its branch was pushed
      (the commits then live on the remote).
    Verification caches are purged first (they are not work)."""
    from ..execution.runner import _purge_caches
    integration = report.worktree
    for info in manager.list():
        try:
            _purge_caches(info.path)
        except Exception:
            pass
    removed, refused = [], []
    for info in manager.list():
        try:
            if info.task_id == integration.task_id:
                if not pushed:
                    raise GhError(f"integration branch {info.branch} was not "
                                  "pushed — keeping its worktree")
                manager.cleanup(info.task_id,
                                merged_into=f"origin/{info.branch}")
            else:
                manager.cleanup(info.task_id, merged_into=integration.branch)
            removed.append(info.task_id)
        except Exception as e:
            refused.append(f"{info.task_id}: {e}")
    result.cleaned = removed
    result.cleanup_refused = refused
