"""DockerRunner — turns a SandboxPolicy into a docker invocation.

Every command passes three gates before any process starts:

1. classify_command → deny / confirm / allow. Denied commands never
   reach docker; confirm-required commands need an explicit approval
   callback (no callback = denied — "要求人工批准").
2. SecretFilter: the container env is built strictly from
   filter_env(os.environ) — SSH keys, cloud credentials, personal
   tokens and the host docker socket never appear in `docker run` env.
3. The docker argv itself: --network none, --cpus/--memory limits,
   --read-only root + tmpfs /tmp, the workspace mounted read-only
   (the ONLY view of the repository), -w /workspace, an unprivileged
   user, and a wall-clock timeout enforced by the runner.

The executor is injectable so the argv/policy translation is fully
unit-testable without a docker daemon (this environment has none — see
DEVELOPMENT_RESULTS.md Phase 8).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .policy import SandboxPolicy
from .security import CommandVerdict, SecretFilter, classify_command


@dataclass
class SandboxResult:
    """The outcome of one sandboxed command."""

    command: str
    verdict: CommandVerdict
    argv: list[str] = field(default_factory=list)   # [] when denied
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    ran: bool = False                               # False = blocked by policy


class DockerRunner:
    """Executes shell commands inside the policy's docker sandbox."""

    def __init__(self, policy: SandboxPolicy, *,
                 docker_bin: str = "docker",
                 env: dict[str, str] | None = None,
                 executor: Callable[..., subprocess.CompletedProcess] | None = None,
                 confirm: Callable[[str, str], bool] | None = None):
        """``executor`` replaces subprocess.run (argv-level tests);
        ``confirm(command, reason)`` is the human-approval gate — without
        it, confirm-required commands are denied."""
        self.policy = policy
        self.docker_bin = docker_bin
        self._env_source = env if env is not None else dict(os.environ)
        self._executor = executor or subprocess.run
        self._confirm = confirm
        self._secret_filter = SecretFilter(policy.env_allowlist)

    # ─── the three gates ───────────────────────────────────────

    def check(self, command: str) -> CommandVerdict:
        return classify_command(command)

    def container_env(self) -> dict[str, str]:
        """The filtered env — what `docker run --env` would receive."""
        return self._secret_filter.filter_env(self._env_source)

    def check_workspace_path(self, path: str | Path) -> CommandVerdict:
        """Repository Root Restriction: tool paths must stay inside the
        policy's workspace."""
        if self.policy.workspace is None:
            return CommandVerdict("deny", "no_workspace",
                                  "no workspace configured for the sandbox")
        from .security import PathTraversalGuard
        return PathTraversalGuard(self.policy.workspace).check(path)

    # ─── execution ─────────────────────────────────────────────

    def run(self, command: str, *, timeout_s: float | None = None,
            workspace: str | Path | None = None,
            cwd: str | Path | None = None) -> SandboxResult:
        """Run one command. ``workspace`` overrides the policy's mount
        root (the DAG executor passes each task's worktree); ``cwd`` is a
        directory inside it, translated to -w /workspace/<rel> (rejected
        via PathTraversalGuard when it escapes)."""
        verdict = self.check(command)
        if verdict.action == "deny":
            return SandboxResult(command=command, verdict=verdict)
        if verdict.action == "confirm":
            if self._confirm is None or not self._confirm(command, verdict.reason):
                return SandboxResult(command=command, verdict=CommandVerdict(
                    "deny", verdict.rule,
                    f"{verdict.reason} — human approval required but not given"))
        ws = (Path(workspace).resolve() if workspace is not None
              else self.policy.workspace)
        if ws is None:
            return SandboxResult(command=command, verdict=CommandVerdict(
                "deny", "no_workspace", "no workspace configured for the sandbox"))
        cwd_rel = ""
        if cwd is not None:
            from .security import PathTraversalGuard
            guard = PathTraversalGuard(ws).check(cwd)
            if guard.action != "allow":
                return SandboxResult(command=command, verdict=guard)
            rel = Path(cwd).resolve().relative_to(ws)
            cwd_rel = "" if str(rel) == "." else str(rel)
        argv = self._build_argv(command, ws, cwd_rel)
        timeout = timeout_s if timeout_s is not None else self.policy.timeout_s
        try:
            p = self._executor(argv, capture_output=True, text=True,
                               timeout=timeout,
                               encoding="utf-8", errors="replace")
            return SandboxResult(
                command=command, verdict=verdict, argv=argv, ran=True,
                exit_code=p.returncode,
                stdout=(p.stdout or "")[:self.policy.max_output_chars],
                stderr=(p.stderr or "")[:self.policy.max_output_chars],
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                command=command, verdict=verdict, argv=argv, ran=True,
                timed_out=True, exit_code=None,
                stdout=(e.stdout or "")[:self.policy.max_output_chars] if e.stdout else "",
                stderr=f"command timed out after {timeout}s",
            )
        except OSError as e:
            return SandboxResult(
                command=command, verdict=verdict, argv=argv, ran=False,
                exit_code=None, stderr=f"could not start sandbox: {e}",
            )

    # ─── argv construction (the policy, as docker flags) ───────

    def _build_argv(self, command: str, ws: Path | None = None,
                    cwd_rel: str = "") -> list[str]:
        p = self.policy
        ws = ws or p.workspace
        argv = [self.docker_bin, "run", "--rm"]
        argv += ["--network", p.network]                 # Network Policy
        argv += ["--cpus", p.cpu_limit]                  # CPU Limit
        argv += ["--memory", p.memory_limit,             # Memory Limit
                 "--memory-swap", p.memory_limit]
        argv += ["--pids-limit", "256"]                  # fork-bomb bound
        argv += ["--user", p.user]                       # never root
        argv += ["--cap-drop", "ALL"]                    # no capabilities
        if p.read_only_root:
            argv += ["--read-only",                      # Disk Restriction
                     "--tmpfs", f"/tmp:rw,size={p.tmpfs_size}"]
        if ws is not None:
            mode = "rw" if p.workspace_writable else "ro"
            argv += ["--volume",                          # Workspace Restriction
                     f"{ws}:/workspace:{mode}"]
        if p.writable is not None:
            argv += ["--volume",
                     f"{Path(p.writable).resolve()}:/workspace-writable:rw"]
        env = self.container_env()                        # Env + Secret Filtering
        for name, value in sorted(env.items()):
            argv += ["--env", f"{name}={value}"]
        argv += ["-w", f"/workspace/{cwd_rel}" if cwd_rel else "/workspace"]
        argv += [p.image]
        argv += ["sh", "-lc", command]                    # command as one arg
        return argv
