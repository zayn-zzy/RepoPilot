"""CommandExecution — the sandbox's actual wiring into command paths.

Verification stages, the agent's shell tool and the repo test/lint tools
all call SandboxedCommandRunner. Three modes:

- "off"  — commands run on the host (the pre-wiring behavior);
- "auto" — docker when the daemon is reachable, else host — and every
  result SAYS which path ran (``sandbox="docker" | "host"`` plus the
  fallback reason). The sandbox status is reported, never faked;
- "on"   — docker required: an unreachable daemon blocks the command
  (callers fail the run up front with a clear message).

The per-thread binding (set_sandbox/get_sandbox) mirrors the work-root
binding in tools.py: each DAG task thread runs its commands against its
own worktree sandbox.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .policy import SandboxPolicy
from .runner import DockerRunner, SandboxResult
from .security import CommandVerdict

_SANDBOX = threading.local()


def set_sandbox(runner: "SandboxedCommandRunner | None") -> None:
    """Bind this thread's shell/tests/lint commands to a sandbox runner
    (None restores direct host execution)."""
    _SANDBOX.runner = runner


def get_sandbox() -> "SandboxedCommandRunner | None":
    """The thread's sandbox runner, or None (= host execution)."""
    return getattr(_SANDBOX, "runner", None)


@dataclass
class CommandResult:
    """The outcome of one command through the sandbox layer. Always says
    which path actually ran it."""

    command: str                        # display form of the command
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    sandbox: str = "host"               # "docker" | "host" | "blocked"
    fallback_reason: str = ""           # why host ran instead of docker
    verdict: CommandVerdict | None = None
    argv: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)   # shared counters (docker/host/blocked)

    def as_completed_process(self) -> subprocess.CompletedProcess:
        """subprocess.CompletedProcess-compatible view (callers that
        consume returncode/stdout/stderr keep working unchanged).
        A blocked command (returncode None) becomes exit 1."""
        return subprocess.CompletedProcess(
            self.command,
            self.returncode if self.returncode is not None else 1,
            self.stdout, self.stderr)


class SandboxedCommandRunner:
    """Policy-gated command execution with an explicit host fallback."""

    def __init__(self, workspace: str | Path, *,
                 policy: SandboxPolicy | None = None,
                 mode: str = "auto",               # off | auto | on
                 docker_bin: str = "docker",
                 executor: Callable[..., subprocess.CompletedProcess] | None = None,
                 availability: Callable[[], bool] | None = None,
                 confirm: Callable[[str, str], bool] | None = None,
                 env: dict[str, str] | None = None,
                 ):
        """``executor`` replaces subprocess.run for the docker argv
        (argv-level tests); ``availability`` replaces the docker-daemon
        probe (probed once, cached)."""
        if mode not in ("off", "auto", "on"):
            raise ValueError(f"unknown sandbox mode {mode!r}")
        self.workspace = Path(workspace).resolve()
        self.policy = policy or SandboxPolicy(workspace=self.workspace)
        self.mode = mode
        self.docker_bin = docker_bin
        self._executor = executor
        self._availability = availability
        self._confirm = confirm
        self._env = env
        self._probed: bool | None = None
        self._probe_reason = ""
        self.stats = {"docker": 0, "host": 0, "blocked": 0}

    # ─── availability ─────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Is the docker daemon reachable? (cached probe)."""
        if self._probed is None:
            self._probed = self._probe()
        return self._probed

    def _probe(self) -> bool:
        if self._availability is not None:
            return self._availability()
        if shutil.which(self.docker_bin) is None:
            self._probe_reason = f"{self.docker_bin} binary not found"
            return False
        try:
            p = subprocess.run([self.docker_bin, "info"], capture_output=True,
                               text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            self._probe_reason = f"docker probe failed: {e}"
            return False
        if p.returncode != 0:
            lines = (p.stderr or p.stdout or "").strip().splitlines()
            self._probe_reason = (lines[-1][:200] if lines
                                  else f"docker info exited {p.returncode}")
            return False
        return True

    # ─── execution ────────────────────────────────────────────

    def run(self, argv_or_command: list[str] | str, *, cwd: str | Path | None = None,
            timeout_s: float | None = None) -> CommandResult:
        """Run a command (argv list, safely joined, or a shell string).
        ``cwd`` must be inside the workspace (host runs get the real
        path; docker runs get /workspace/<rel>)."""
        argv = list(argv_or_command) if isinstance(argv_or_command, (list, tuple)) \
            else argv_or_command
        display = argv if isinstance(argv, str) else " ".join(shlex.quote(a) for a in argv)
        host_cwd = Path(cwd).resolve() if cwd is not None else self.workspace

        if self.mode == "off":
            return self._run_host(argv, host_cwd, display, timeout_s)
        if self.available:
            return self._run_docker(argv, display, host_cwd, timeout_s)
        if self.mode == "on":
            self.stats["blocked"] += 1
            return CommandResult(
                command=display, returncode=None, sandbox="blocked",
                stderr=(f"sandbox mode 'on' but docker is unavailable: "
                        f"{self._probe_reason or 'daemon unreachable'}"),
                stats=self.stats)
        # auto: host fallback, and the result says so.
        result = self._run_host(argv, host_cwd, display, timeout_s)
        result.fallback_reason = (self._probe_reason or "docker unavailable")
        return result

    def _run_docker(self, argv, display: str, host_cwd: Path,
                    timeout_s: float | None) -> CommandResult:
        docker = DockerRunner(self.policy, docker_bin=self.docker_bin,
                              env=self._env, executor=self._executor,
                              confirm=self._confirm)
        # `display` is the safely-quoted form (argv joined with shlex.quote;
        # a string passes through unchanged) — args with spaces survive the
        # inner `sh -lc`.
        sr: SandboxResult = docker.run(display, timeout_s=timeout_s,
                                       workspace=self.workspace, cwd=host_cwd)
        if not sr.ran:
            self.stats["blocked"] += 1
            return CommandResult(
                command=display, returncode=sr.exit_code, sandbox="blocked",
                stdout=sr.stdout, stderr=sr.stderr, verdict=sr.verdict,
                argv=sr.argv, stats=self.stats)
        self.stats["docker"] += 1
        return CommandResult(
            command=display, returncode=sr.exit_code, sandbox="docker",
            stdout=sr.stdout, stderr=sr.stderr, verdict=sr.verdict,
            argv=sr.argv, stats=self.stats)

    def _run_host(self, argv, host_cwd: Path, display: str,
                  timeout_s: float | None) -> CommandResult:
        self.stats["host"] += 1
        kwargs = {}
        if isinstance(argv, str):
            kwargs["shell"] = True
        try:
            # errors="replace": command output is not guaranteed UTF-8 —
            # a stray byte must never crash the caller (it becomes U+FFFD,
            # the same policy as the file tools).
            p = subprocess.run(argv, cwd=str(host_cwd), capture_output=True,
                               text=True, timeout=timeout_s, **kwargs,
                               encoding="utf-8", errors="replace")
            return CommandResult(command=display, returncode=p.returncode,
                                 stdout=p.stdout or "", stderr=p.stderr or "",
                                 sandbox="host", stats=self.stats)
        except subprocess.TimeoutExpired as e:
            return CommandResult(command=display, returncode=None,
                                 stdout=e.stdout or "",
                                 stderr=f"command timed out after {timeout_s}s",
                                 sandbox="host", stats=self.stats)
        except (OSError, subprocess.SubprocessError) as e:
            return CommandResult(command=display, returncode=None,
                                 stderr=f"{type(e).__name__}: {e}",
                                 sandbox="host", stats=self.stats)

    @property
    def label(self) -> str:
        """One honest line about where commands run (no counters — safe
        to record before the first command)."""
        if self.mode == "off":
            return "off (host execution)"
        if self.available:
            return f"{self.mode} (docker)"
        return (f"{self.mode} (host fallback — "
                f"{self._probe_reason or 'docker unavailable'})")

    def describe(self) -> str:
        """The label plus the real per-run counters."""
        return (f"sandbox {self.label}; commands: {self.stats['docker']} docker, "
                f"{self.stats['host']} host, {self.stats['blocked']} blocked")
