"""ToolRegistry — per-instance tool registration, dispatch, and deferred
activation. Fixes the original design's module-global state: every runtime
owns its registry, so two agents can have different active tool sets.

Dispatch reuses tools.execute_tool for built-ins (keeping read-before-edit
and mtime bookkeeping); custom tools run their own handler."""

from __future__ import annotations

import inspect
import json
from typing import Iterable

from ..tools import (
    BUILTIN_TOOL_HANDLERS,
    ToolDef,
    execute_tool,
    tool_definitions,
)
from .tools import Tool, tool_from_definition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._activated: set[str] = set()

    # ─── Registration ────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> bool:
        self._activated.discard(name)
        return self._tools.pop(name, None) is not None

    # ─── Lookup ──────────────────────────────────────────────

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def contains(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    # ─── Definitions (wire format for the LLM API) ───────────

    def definitions(self) -> list[ToolDef]:
        """All registered tools as schema dicts (deferred marker included)."""
        return [t.to_definition() for t in self._tools.values()]

    def active_definitions(self) -> list[ToolDef]:
        """Schema dicts for the tools currently exposed to the model —
        deferred tools are excluded until activated (via tool_search)."""
        return [
            t.to_definition()
            for t in self._tools.values()
            if not t.deferred or t.name in self._activated
        ]

    def activate(self, names: Iterable[str]) -> None:
        self._activated.update(names)

    # ─── Dispatch ────────────────────────────────────────────

    async def dispatch(
        self,
        name: str,
        inp: dict,
        *,
        acl=None,
        read_file_state: dict[str, float] | None = None,
    ) -> str:
        """Execute one tool call. Returns the result string (never raises for
        unknown/denied tools — the loop turns the string into a tool_result)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"

        if acl is not None:
            perm = acl.check(name, inp)
            if perm["action"] != "allow":
                # Programmatic dispatch has no interactive confirmer; anything
                # not auto-allowed is denied here (the loop's confirm flow runs
                # before dispatch when driven through an Agent).
                return f"Action denied: {perm.get('message', '')}"

        # Deferred tool activation lives in the registry so each instance
        # keeps its own activated set (the module-level tool_search would
        # leak activations across agents).
        if name == "tool_search":
            return self._handle_tool_search(inp)

        # Built-ins go through the original executor to preserve its
        # read-before-edit and mtime tracking.
        if name in BUILTIN_TOOL_HANDLERS:
            return await execute_tool(name, inp, read_file_state)

        result = tool.handler(inp)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    def _handle_tool_search(self, inp: dict) -> str:
        query = (inp.get("query") or "").lower()
        matches = [
            t
            for t in self._tools.values()
            if t.deferred
            and (query in t.name.lower() or query in t.description.lower())
        ]
        if not matches:
            return "No matching deferred tools found."
        for m in matches:
            self._activated.add(m.name)
        return json.dumps(
            [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in matches
            ],
            indent=2,
        )


def build_default_registry() -> ToolRegistry:
    """A fresh registry seeded with all built-in tool definitions. Tools that
    only the Agent loop itself implements (agent/skill/plan mode) get a
    placeholder handler — the loop intercepts them before dispatch."""
    registry = ToolRegistry()
    for defn in tool_definitions:
        name = defn["name"]
        if name in BUILTIN_TOOL_HANDLERS:
            handler = BUILTIN_TOOL_HANDLERS[name]
        else:
            handler = lambda inp, _name=name: f"{_name} is handled by the agent loop"

        registry.register(tool_from_definition(defn, handler))
    return registry
