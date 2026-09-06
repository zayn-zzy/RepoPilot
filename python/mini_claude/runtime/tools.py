"""Tool base interface — typed wrapper around the legacy dict-based ToolDefs.

The original tool system is a module-global list of plain dicts dispatched
through a handler table. `Tool` gives that a stable interface while keeping
the dict as the wire format (to_definition), so the original Agent loop and
the LLM API contract are untouched."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Union

from ..tools import CONCURRENCY_SAFE_TOOLS, READ_TOOLS, ToolDef

ToolHandler = Union[Callable[[dict], str], Callable[[dict], Awaitable[str]]]


@dataclass(frozen=True)
class Tool:
    """Base interface every registered tool implements."""

    name: str
    description: str
    input_schema: dict
    handler: ToolHandler
    read_only: bool = False
    concurrency_safe: bool = False
    deferred: bool = False

    def to_definition(self) -> ToolDef:
        """Render as the Anthropic tool schema dict the loop sends to the API."""
        defn: ToolDef = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if self.deferred:
            defn["deferred"] = True
        return defn


def tool_from_definition(defn: ToolDef, handler: ToolHandler) -> Tool:
    """Adapt a legacy tool_definitions entry into the Tool interface."""
    return Tool(
        name=defn["name"],
        description=defn.get("description", ""),
        input_schema=defn.get("input_schema", {"type": "object", "properties": {}}),
        handler=handler,
        read_only=defn["name"] in READ_TOOLS,
        concurrency_safe=defn["name"] in CONCURRENCY_SAFE_TOOLS,
        deferred=bool(defn.get("deferred")),
    )
