"""The five role definitions — each role gets an independent prompt, an
independent tool ACL, its own registry, context, and budget (via the
Phase 1 AgentRuntime). No other roles exist by design."""

from __future__ import annotations

from ..runtime import AgentRuntime, ToolACL, ToolRegistry, build_default_registry
from ..runtime.config import AgentConfig
from .tools import READ_SAFE_REPO_TOOLS, make_publish_tool, make_repo_tools

ROLE_NAMES = ("planner", "explorer", "coder", "tester", "reviewer")

ROLE_PROMPTS: dict[str, str] = {
    "planner": """You are the Planner agent — the first step of a software-engineering pipeline.
You are READ-ONLY: you may only read code and search the repository, never modify anything.
Analyze the requirement, inspect the relevant code, and produce a concrete step-by-step plan.
When your plan is complete, call publish_artifact with kind="plan" and payload:
{"tasks": [{"id": "...", "title": "...", "agent_role": "coder|tester|explorer", "description": "..."}],
 "summary": "...", "related_files": ["..."]}
Then end your turn.""",
    "explorer": """You are the Explorer agent — a read-only code-search specialist.
Use the search tools (grep_search, symbol_search, dependency_search, semantic_search,
git_log) to map the code relevant to the task. NEVER modify files.
When done, call publish_artifact with kind="exploration" and payload:
{"findings": ["..."], "related_files": ["..."], "symbols": ["..."]}
Then end your turn.""",
    "coder": """You are the Coder agent — you implement the plan.
You may read, search, edit files, and run shell commands. Work through the plan's tasks,
edit/write the code, and verify syntax as you go. Be efficient: a few focused reads and
edits, not endless exploration.
As soon as the change is complete, call publish_artifact with kind="code_change" and payload:
{"files_modified": ["..."], "changes": ["one line per change"], "test_commands": ["..."]}
Then end your turn. Do NOT keep exploring after the code is written.""",
    "tester": """You are the Tester agent — you verify the code change.
Run the test commands with run_tests, run lint checks, and parse any failures with
parse_failure. You may read files and run shell commands.
When done, call publish_artifact with kind="test_report" and payload:
{"passed": true|false, "failed_tests": ["..."], "summary": "..."}
Then end your turn.""",
    "reviewer": """You are the Reviewer agent — the final quality gate. You are READ-ONLY.
Review the code change and test report: check the diff (git_diff), inspect dependencies
(dependency_search), and judge correctness/risk.
Call publish_artifact with kind="review" and payload:
{"approved": true|false, "issues": ["..."], "suggestions": ["..."]}
Then end your turn.""",
}

# Tool ACL per role — matches the spec: planner/explorer/reviewer are
# read-only, coder writes, tester verifies. publish_artifact is the shared
# structured-output channel.
ROLE_TOOL_SETS: dict[str, frozenset[str]] = {
    "planner": frozenset({
        "read_file", "list_files", "grep_search", "tool_search",
        "symbol_search", "dependency_search", "semantic_search", "git_log",
        "publish_artifact",
    }),
    "explorer": frozenset({
        "read_file", "list_files", "grep_search", "tool_search",
        "symbol_search", "dependency_search", "semantic_search", "git_log",
        "publish_artifact",
    }),
    "coder": frozenset({
        "read_file", "write_file", "edit_file", "list_files", "grep_search",
        "tool_search", "run_shell", "git_diff", "git_log",
        "symbol_search", "dependency_search", "semantic_search",
        "publish_artifact",
    }),
    "tester": frozenset({
        "read_file", "list_files", "grep_search", "tool_search", "run_shell",
        "run_tests", "run_lint", "parse_failure",
        "publish_artifact",
    }),
    "reviewer": frozenset({
        "read_file", "list_files", "grep_search", "tool_search",
        "git_diff", "git_log", "symbol_search", "dependency_search",
        "semantic_search", "publish_artifact",
    }),
}

READ_ONLY_ROLES = frozenset({"planner", "explorer", "reviewer"})


def build_role_registry(index, mailbox, git_root=None) -> ToolRegistry:
    """A full registry (built-ins + repo tools + publish) for one team. Every
    role agent shares the registry object's content but filters through its
    own ACL; the registry itself carries the repo-backed tool handlers.
    ``git_root`` binds git/shell/file tools to a specific checkout (Phase 6
    passes the task worktree)."""
    registry = build_default_registry()
    for tool in make_repo_tools(index, git_root=git_root):
        registry.register(tool)
    registry.register(make_publish_tool(mailbox))
    return registry


def role_acl(role: str, permission_mode: str = "default") -> ToolACL:
    """The ACL for one role: explicit allowlist + read-only backstop for the
    three read-only roles (extended with the read-safe repo tools)."""
    return ToolACL(
        read_only=role in READ_ONLY_ROLES,
        allowed_tools=ROLE_TOOL_SETS[role],
        permission_mode=permission_mode,
        read_safe_tools=READ_SAFE_REPO_TOOLS,
    )


def build_role_runtime(role: str, index, mailbox, *, model: str, api_key: str | None,
                       registry: ToolRegistry | None = None,
                       interactive: bool = False,
                       **config_overrides) -> AgentRuntime:
    """One independent AgentRuntime for a role: its own prompt, ACL, context
    and budget. The caller owns the shared registry/mailbox."""
    if role not in ROLE_PROMPTS:
        raise ValueError(f"unknown role {role!r} (expected one of {ROLE_NAMES})")
    config = AgentConfig(
        role=role,
        model=model,
        api_key=api_key,
        custom_system_prompt=ROLE_PROMPTS[role],
        read_only=role in READ_ONLY_ROLES,
        interactive=interactive,
        **config_overrides,
    )
    return AgentRuntime(
        config,
        registry=registry if registry is not None else build_role_registry(index, mailbox),
        acl=role_acl(role, permission_mode=config.permission_mode),
    )
