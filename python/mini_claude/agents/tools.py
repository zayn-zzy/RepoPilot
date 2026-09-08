"""Role tools — the repository/search/publish tools the five agents use.

Read-safe repository tools (symbol/dependency/semantic search, git log/diff)
are declared read_only=True so read-only roles can use them; publish_artifact
lets an agent hand structured output to the pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..retrieval import SemanticRetriever
from ..runtime import Tool, ToolRegistry

# Tools a read-only role may use (registered via ToolACL(read_safe_tools=...)).
READ_SAFE_REPO_TOOLS = frozenset({
    "symbol_search", "dependency_search", "semantic_search",
    "git_log", "git_diff", "publish_artifact",
})


def _format_symbols(symbols) -> str:
    if not symbols:
        return "No symbols found."
    lines = [
        f"{s.qualified_name}  ({s.kind.value})  {s.location.file_path}:{s.location.start_line}"
        for s in symbols[:30]
    ]
    if len(symbols) > 30:
        lines.append(f"... and {len(symbols) - 30} more")
    return "\n".join(lines)


def make_repo_tools(index, git_root=None) -> list[Tool]:
    """Tools backed by the Phase 2 RepositoryIndex (+ git for logs/diffs).
    All of them are read-only; they never modify the repository.

    ``git_root`` (default: the index root) is where git and shell commands
    run and where files are read from — Phase 6 binds it to a task's
    worktree so git_diff/git_log/run_tests see exactly that task's state.
    Symbol/dependency search still serves the index snapshot."""
    root = Path(git_root) if git_root is not None else index.root
    semantic_cache: dict[str, SemanticRetriever] = {}

    def symbol_search(inp: dict) -> str:
        kind = inp.get("kind")
        return _format_symbols(index.find_symbol(str(inp.get("name", "")), kind))

    def dependency_search(inp: dict) -> str:
        path = str(inp.get("file_path", ""))
        deps = index.file_dependencies(path)
        dependents = index.dependents(path)
        lines = [f"dependencies of {path}: {deps or '[]'}",
                 f"dependents of {path}: {dependents or '[]'}"]
        module = index.graph.module_of_file(path)
        if module:
            lines.append(f"module dependencies of {module}: {index.module_dependencies(module)}")
        return "\n".join(lines)

    def semantic_search(inp: dict) -> str:
        query = str(inp.get("query", ""))
        top_k = int(inp.get("top_k", 5))
        retriever = semantic_cache.setdefault(
            "default",
            SemanticRetriever({p: (root / p).read_text(errors="replace")
                               for p in index.files()}),
        )
        hits = retriever.search(query, top_k=top_k)
        return "\n".join(f"{h.file_path}  ({h.score:.3f})" for h in hits) or "No results."

    def git_log(inp: dict) -> str:
        max_count = int(inp.get("max_count", 10))
        return _run_git(["log", "--oneline", f"-{max_count}"], root)

    def git_diff(inp: dict) -> str:
        stat = _run_git(["diff", "--stat"], root)
        full = _run_git(["diff"], root)
        # _run_git maps an empty (successful) git output to its "(no output)"
        # sentinel — treat that as an empty diff, not as text to show.
        if full in ("", "(no output)"):
            return "No changes in the working tree."
        return f"{stat}\n\n{full}"

    def run_tests(inp: dict) -> str:
        command = str(inp.get("command") or "python -m pytest -q")
        return _run_shell(command, root, int(inp.get("timeout", 120000)))

    def run_lint(inp: dict) -> str:
        command = str(inp.get("command") or "python -m py_compile $(git ls-files '*.py' 2>/dev/null) 2>/dev/null || true")
        return _run_shell(command, root, int(inp.get("timeout", 60000)))

    def parse_failure(inp: dict) -> str:
        from ..planning import RequirementParser

        failure = RequirementParser().parse_test_failure(str(inp.get("output", "")))
        if not failure.failed_tests and not failure.assertion_message:
            return "No test failures recognized in the output."
        lines = [f"failed tests: {failure.failed_tests or '[]'}"]
        if failure.assertion_message:
            lines.append(f"assertion: {failure.assertion_message}")
        return "\n".join(lines)

    schema = {"type": "object", "properties": {}}
    return [
        Tool("symbol_search",
             "Search the repository symbol index by name (optional kind: function/class/method).",
             {"type": "object", "properties": {
                 "name": {"type": "string", "description": "symbol name to search"},
                 "kind": {"type": "string", "description": "optional: function|class|method"},
             }, "required": ["name"]},
             symbol_search, read_only=True),
        Tool("dependency_search",
             "Show the import dependencies and dependents of a file (or module).",
             {"type": "object", "properties": {
                 "file_path": {"type": "string", "description": "repo-relative file path"},
             }, "required": ["file_path"]},
             dependency_search, read_only=True),
        Tool("semantic_search",
             "Semantic (meaning-based) search over the repository files.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "natural-language query"},
                 "top_k": {"type": "number", "description": "results to return (default 5)"},
             }, "required": ["query"]},
             semantic_search, read_only=True),
        Tool("git_log",
             "Show recent commit history.",
             {"type": "object", "properties": {
                 "max_count": {"type": "number", "description": "commits to show (default 10)"},
             }},
             git_log, read_only=True),
        Tool("git_diff",
             "Show the current working-tree diff.",
             {"type": "object", "properties": {}},
             git_diff, read_only=True),
        Tool("run_tests",
             "Run the test suite and return its output.",
             {"type": "object", "properties": {
                 "command": {"type": "string", "description": "test command (default: python -m pytest -q)"},
             }},
             run_tests, read_only=False),
        Tool("run_lint",
             "Run a lint/syntax check and return its output.",
             {"type": "object", "properties": {
                 "command": {"type": "string", "description": "lint command override"},
             }},
             run_lint, read_only=False),
        Tool("parse_failure",
             "Parse test/lint failure output into structured failed tests.",
             {"type": "object", "properties": {
                 "output": {"type": "string", "description": "raw command output"},
             }, "required": ["output"]},
             parse_failure, read_only=True),
    ]


def make_publish_tool(mailbox) -> Tool:
    """The structured-output channel: the agent calls it with a kind and a
    JSON payload; the orchestrator reads the artifact from the mailbox."""

    def publish_artifact(inp: dict) -> str:
        from .artifact import AgentArtifact

        kind = str(inp.get("kind", ""))
        payload = inp.get("payload") or {}
        if not isinstance(payload, dict):
            return "Error: payload must be a JSON object"
        try:
            artifact = AgentArtifact(kind=kind, producer=str(inp.get("producer", "unknown")), payload=payload)
        except Exception as e:
            return f"Error publishing artifact: {e}"
        mailbox.publish(artifact)
        return f"Artifact published: kind={kind} id={artifact.artifact_id}"

    return Tool(
        "publish_artifact",
        "Publish a structured result artifact for the next agent in the pipeline. "
        "kind must be one of: plan, exploration, code_change, test_report, review.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "description": "artifact kind"},
            "producer": {"type": "string", "description": "your role name"},
            "payload": {"type": "object", "description": "structured JSON payload"},
        }, "required": ["kind", "payload"]},
        publish_artifact,
        read_only=True,
    )


# ─── Shell helpers (run_tests / run_lint / git) ─────────────


def _run_git(args: list[str], cwd) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no output)"
    except Exception as e:
        return f"Error running git: {e}"


def _run_shell(command: str, cwd, timeout_ms: int) -> str:
    # Phase 12: run_tests/run_lint go through the thread's sandbox runner
    # (docker) when the DAG executor bound one — with the honest host
    # fallback note otherwise; without a sandbox the behavior is the
    # pre-wiring host subprocess.
    from ..sandbox import get_sandbox
    from ..tools import _format_sandbox_result
    sandbox = get_sandbox()
    if sandbox is not None:
        try:
            result = sandbox.run(command, cwd=cwd, timeout_s=timeout_ms / 1000)
            return _format_sandbox_result(result, timeout_ms)
        except Exception as e:
            return f"Error: {e}"
    try:
        result = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout_ms / 1000,
        )
        out = result.stdout or ""
        if result.returncode != 0:
            err = f"\nStderr: {result.stderr}" if result.stderr else ""
            return f"Command failed (exit {result.returncode}){out}{err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_ms}ms"
    except Exception as e:
        return f"Error: {e}"
