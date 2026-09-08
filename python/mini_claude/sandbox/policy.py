"""SandboxPolicy — every sandbox dimension in one dataclass.

The doc's list maps one-to-one onto fields:

    Docker Runner           → DockerRunner
    CPU Limit               → cpu_limit   (docker --cpus)
    Memory Limit            → memory_limit (docker --memory/--memory-swap)
    Timeout                 → timeout_s   (wall clock, enforced by the runner)
    Disk/Workspace Restriction → workspace (read-only mount) + writable
                                 (the only writable mount) + --read-only
                                 root filesystem + /tmp tmpfs
    Network Policy          → network ("none" by default)
    Environment Filtering   → env_allowlist (strict; see SecretFilter)
    Secret Filtering        → SecretFilter (never-inject deny-list)
    Permission Rules        → classify_command verdicts (deny/confirm/allow)
    Dangerous Command Detection → security.classify_command
    Path Traversal Protection → security.PathTraversalGuard
    Repository Root Restriction → workspace mount is the only view of the
                                 repo, read-only, and tool paths are checked
                                 against it with the guard
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .security import DEFAULT_ENV_ALLOWLIST


@dataclass
class SandboxPolicy:
    image: str = "python:3.12-slim"
    cpu_limit: str = "1.0"          # docker --cpus
    memory_limit: str = "1g"        # docker --memory / --memory-swap
    tmpfs_size: str = "256m"        # in-container /tmp scratch (disk bound)
    timeout_s: float = 300.0        # per-command wall clock
    network: str = "none"           # none | bridge | host
    workspace: Path | None = None   # repo root, mounted at /workspace
    workspace_writable: bool = False  # False = read-only mount (the doc's
                                      # default); True = task worktrees that
                                      # tests may write caches into
    writable: Path | None = None    # optional writable results dir (e.g. worktree)
    user: str = "1000:1000"         # run as unprivileged uid:gid, never root
    env_allowlist: frozenset[str] = field(default_factory=lambda: DEFAULT_ENV_ALLOWLIST)
    read_only_root: bool = True     # container root filesystem read-only
    max_output_chars: int = 100_000  # stdout/stderr kept per command
