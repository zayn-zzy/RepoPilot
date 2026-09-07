"""Tool ACL — role-level tool access control layered on top of the original
static permission engine (tools.check_permission).

check_permission knows modes and deny/allow rules but nothing about roles.
The ACL adds the role layer: read-only enforcement, per-role tool allow/deny
sets, and definition filtering so the model never sees tools it can't use."""

from __future__ import annotations

from typing import Iterable

from ..tools import EDIT_TOOLS, READ_TOOLS, ToolDef, check_permission

# Tools a read-only role must not run even if the permission mode would allow
# them: file writers, shell, and the two fork tools (agent spawns sub-agents
# with write access; fork-context skills inherit the child permission mode).
WRITE_CAPABLE_TOOLS = EDIT_TOOLS | {"run_shell", "agent", "skill"}

READ_SAFE_TOOLS = READ_TOOLS | {"enter_plan_mode", "exit_plan_mode", "tool_search"}


class ToolACL:
    """Role-level access control for one runtime instance."""

    def __init__(
        self,
        *,
        read_only: bool = False,
        allowed_tools: Iterable[str] | None = None,
        denied_tools: Iterable[str] | None = None,
        permission_mode: str = "default",
        plan_file_path: str | None = None,
        read_safe_tools: Iterable[str] | None = None,
    ):
        self.read_only = read_only
        self.allowed_tools: set[str] | None = (
            set(allowed_tools) if allowed_tools is not None else None
        )
        self.denied_tools: set[str] | None = (
            set(denied_tools) if denied_tools is not None else None
        )
        self.permission_mode = permission_mode
        self.plan_file_path = plan_file_path
        # Read-only roles may use extra tools that don't modify anything
        # (later phases register repo search / git read tools here).
        self._read_safe = READ_SAFE_TOOLS | set(read_safe_tools or ())

    def check(self, tool_name: str, inp: dict) -> dict:
        """Return {"action": "allow"|"deny", "message"} for one tool call.
        Role gates run first; then the static engine (deny rules, mode
        semantics, dangerous-command confirmation) has the final say."""
        if self.denied_tools is not None and tool_name in self.denied_tools:
            return {"action": "deny", "message": f"{tool_name} is denied by this agent's tool ACL"}
        if self.read_only and tool_name not in self._read_safe:
            return {
                "action": "deny",
                "message": f"{tool_name} is write-capable and this agent is read-only",
            }
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return {"action": "deny", "message": f"{tool_name} is not in this agent's tool ACL"}
        return check_permission(tool_name, inp, self.permission_mode, self.plan_file_path)

    def filter_definitions(self, definitions: list[ToolDef]) -> list[ToolDef]:
        """Definitions that pass the role gates — what the model is shown."""
        return [d for d in definitions if self._definition_allowed(d["name"])]

    def _definition_allowed(self, tool_name: str) -> bool:
        if self.denied_tools is not None and tool_name in self.denied_tools:
            return False
        if self.read_only and tool_name not in self._read_safe:
            return False
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False
        return True
