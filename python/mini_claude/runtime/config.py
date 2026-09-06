"""AgentConfig — validated, per-role configuration for one AgentRuntime instance.

Converges the original Agent's 11 keyword constructor arguments into one
validated dataclass, plus the role-level ACL knobs (read_only / tool_names)
that Phase 5's multi-agent roles build on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..tools import tool_definitions

PERMISSION_MODES = ("default", "plan", "acceptEdits", "bypassPermissions", "dontAsk", "auto")

ROLE_NAMES = ("general", "planner", "explorer", "coder", "tester", "reviewer")

# Role tool sets mirror the Phase 5 role ACLs. Read-only roles get the core
# read tools; write roles add edit/write/shell. None = unrestricted (general).
ROLE_PROFILES: dict[str, dict[str, Any]] = {
    "general": {"read_only": False, "tool_names": None},
    "planner": {"read_only": True, "tool_names": ("read_file", "list_files", "grep_search", "tool_search")},
    "explorer": {"read_only": True, "tool_names": ("read_file", "list_files", "grep_search", "tool_search")},
    "coder": {
        "read_only": False,
        "tool_names": (
            "read_file", "list_files", "grep_search", "tool_search",
            "write_file", "edit_file", "run_shell",
        ),
    },
    "tester": {
        "read_only": False,
        "tool_names": ("read_file", "list_files", "grep_search", "tool_search", "run_shell"),
    },
    "reviewer": {"read_only": True, "tool_names": ("read_file", "list_files", "grep_search", "tool_search")},
}

KNOWN_TOOL_NAMES = frozenset(t["name"] for t in tool_definitions)


@dataclass
class AgentConfig:
    """Validated configuration for a single AgentRuntime instance."""

    role: str = "general"
    model: str = "claude-opus-4-6"
    permission_mode: str = "default"
    api_base: str | None = None          # OpenAI-compatible backend (enables it)
    anthropic_base_url: str | None = None
    api_key: str | None = None
    thinking: bool = False
    max_cost_usd: float | None = None
    max_turns: int | None = None
    custom_system_prompt: str | None = None
    tool_names: tuple[str, ...] | None = None  # None = all built-in tools
    read_only: bool = False
    interactive: bool = False

    def validate(self) -> "AgentConfig":
        errors: list[str] = []
        if not isinstance(self.model, str) or not self.model.strip():
            errors.append(f"model must be a non-empty string, got {self.model!r}")
        if self.role not in ROLE_NAMES:
            errors.append(f"unknown role {self.role!r} (expected one of {ROLE_NAMES})")
        if self.permission_mode not in PERMISSION_MODES:
            errors.append(f"invalid permission_mode {self.permission_mode!r} (expected one of {PERMISSION_MODES})")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            errors.append(f"max_cost_usd must be >= 0, got {self.max_cost_usd}")
        if self.max_turns is not None and self.max_turns < 1:
            errors.append(f"max_turns must be >= 1, got {self.max_turns}")
        if self.read_only and self.permission_mode == "bypassPermissions":
            errors.append("read_only conflicts with permission_mode='bypassPermissions'")
        if self.tool_names is not None:
            unknown = set(self.tool_names) - KNOWN_TOOL_NAMES
            if unknown:
                errors.append(f"unknown tool names in tool_names: {sorted(unknown)}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @classmethod
    def from_role(cls, role: str, **overrides: Any) -> "AgentConfig":
        """Build a config from a role profile; overrides are applied on top."""
        if role not in ROLE_PROFILES:
            raise ValueError(f"unknown role {role!r} (expected one of {tuple(ROLE_PROFILES)})")
        profile = ROLE_PROFILES[role]
        tool_names = profile.get("tool_names")
        if tool_names is not None:
            tool_names = tuple(tool_names)
        return cls(role=role, read_only=bool(profile["read_only"]), tool_names=tool_names, **overrides)
