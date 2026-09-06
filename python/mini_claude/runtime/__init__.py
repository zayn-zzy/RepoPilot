"""RepoPilot Agent Runtime — reusable per-role runtime over the original
mini_claude Agent loop. See runtime.runtime for the entry point."""

from .budget import Budget, BudgetStatus
from .config import ROLE_NAMES, ROLE_PROFILES, AgentConfig
from .context import AgentContext, Context
from .events import AgentEvents, EventEmitter
from .permissions import ToolACL
from .provider import LLMProvider
from .registry import ToolRegistry, build_default_registry
from .runtime import AgentRuntime, RunResult
from .tools import Tool, ToolHandler, tool_from_definition
from .trace import Trace, TraceEvent

__all__ = [
    "AgentConfig",
    "AgentContext",
    "AgentEvents",
    "AgentRuntime",
    "Budget",
    "BudgetStatus",
    "Context",
    "EventEmitter",
    "LLMProvider",
    "ROLE_NAMES",
    "ROLE_PROFILES",
    "RunResult",
    "Tool",
    "ToolACL",
    "ToolHandler",
    "ToolRegistry",
    "Trace",
    "TraceEvent",
    "build_default_registry",
    "tool_from_definition",
]
