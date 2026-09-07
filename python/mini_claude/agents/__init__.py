"""RepoPilot Multi-Agent — the five-role team (Planner / Explorer / Coder /
Tester / Reviewer) running as independent AgentRuntimes that exchange
structured AgentArtifacts through a mailbox. One fixed pipeline pass, no
free-form inter-agent chat."""

from .artifact import ARTIFACT_KINDS, AgentArtifact, ArtifactError, ArtifactMailbox
from .roles import (
    READ_ONLY_ROLES,
    ROLE_NAMES,
    ROLE_PROMPTS,
    ROLE_TOOL_SETS,
    build_role_registry,
    build_role_runtime,
    role_acl,
)
from .team import ARTIFACT_KINDS_BY_ROLE, RoleOutcome, TeamConfig, TeamResult, TeamRunner
from .tools import READ_SAFE_REPO_TOOLS, make_publish_tool, make_repo_tools

__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_KINDS_BY_ROLE",
    "AgentArtifact",
    "ArtifactError",
    "ArtifactMailbox",
    "READ_ONLY_ROLES",
    "READ_SAFE_REPO_TOOLS",
    "ROLE_NAMES",
    "ROLE_PROMPTS",
    "ROLE_TOOL_SETS",
    "RoleOutcome",
    "TeamConfig",
    "TeamResult",
    "TeamRunner",
    "build_role_registry",
    "build_role_runtime",
    "make_publish_tool",
    "make_repo_tools",
    "role_acl",
]
