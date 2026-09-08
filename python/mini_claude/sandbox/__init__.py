"""Phase 8 — Docker Sandbox + Security.

Every command that touches the repository runs through a SandboxPolicy:
dangerous-command detection (default-deny list), secret/env filtering
(SSH keys, cloud credentials, tokens and the host docker socket never
reach the sandbox by default), path-traversal and repository-root
restriction, and a DockerRunner that turns the policy into concrete
docker flags (CPU/memory limits, wall-clock timeout, read-only
workspace mount, network=none)."""

from .command import (  # noqa: F401
    CommandResult,
    SandboxedCommandRunner,
    get_sandbox,
    set_sandbox,
)
from .policy import SandboxPolicy  # noqa: F401
from .runner import DockerRunner, SandboxResult  # noqa: F401
from .security import (  # noqa: F401
    CommandVerdict,
    PathTraversalGuard,
    SecretFilter,
    classify_command,
    resolve_inside,
)

__all__ = [
    "SandboxPolicy",
    "DockerRunner",
    "SandboxResult",
    "SandboxedCommandRunner",
    "CommandResult",
    "set_sandbox",
    "get_sandbox",
    "CommandVerdict",
    "classify_command",
    "PathTraversalGuard",
    "SecretFilter",
    "resolve_inside",
]
