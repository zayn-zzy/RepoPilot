"""The `repopilot` CLI — the product surface of RepoPilot.

    repopilot init        prepare a repository (.repopilot/ config)
    repopilot index       build and persist the repository index
    repopilot ask         answer a question with retrieval + LLM context
    repopilot plan        parse a requirement and produce a plan
    repopilot run         run a requirement through its task DAG in parallel worktrees
    repopilot issue       run a GitHub issue through the full flow (run + push + PR)
    repopilot graph       print the dependency graph
    repopilot benchmark   run the Phase 9 evaluation (retrieval by default)

Every command fails with a clear message instead of pretending:
missing git repo, missing API key, missing tools are reported as-is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from ..application.repository import REPOPILOT_VERSION  # noqa: E402


def _load_env_file(path: str | Path = ".env") -> None:
    """Minimal .env loader (no external dependency): KEY=VALUE lines and
    # comments. Existing environment variables are never overridden, and
    nothing is printed — values stay out of logs."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _llm_base_url() -> str | None:
    """Re-export of application.llm.llm_base_url (Web Phase 1: the CLI
    and the services share one implementation — kept under this name
    for the existing CLI tests)."""
    from ..application.llm import llm_base_url
    return llm_base_url()


def main(argv: list[str] | None = None) -> int:
    _load_env_file()  # .env in the current directory (never overrides the real env)
    parser = argparse.ArgumentParser(
        prog="repopilot",
        description="RepoPilot — autonomous software engineering for "
                    "large code repositories.",
    )
    parser.add_argument("--version", action="version",
                        version=f"repopilot {REPOPILOT_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="prepare a repository for RepoPilot")
    p_init.add_argument("dir", nargs="?", default=".")

    p_index = sub.add_parser("index", help="build and persist the repository index")
    p_index.add_argument("dir", nargs="?", default=".")
    p_index.add_argument("--full", action="store_true",
                         help="force a full rebuild, ignoring the saved index "
                              "(default: load .repopilot/index.db and re-parse "
                              "only changed files)")

    p_ask = sub.add_parser("ask", help="answer a question using retrieval + LLM")
    p_ask.add_argument("question", help="the question about this repository")
    p_ask.add_argument("--dir", default=".", help="repository root")
    p_ask.add_argument("--model", default="deepseek-v4-pro[1m]")
    p_ask.add_argument("--semantic", choices=("auto", "local", "api", "lsa"),
                       default="auto",
                       help="semantic embedding backend (default auto: "
                            "API config → local fastembed → LSA fallback)")

    p_plan = sub.add_parser("plan", help="parse a requirement and produce a plan")
    p_plan.add_argument("requirement", help="natural-language requirement")
    p_plan.add_argument("--llm", action="store_true",
                        help="use the LLM planner (default: deterministic)")
    p_plan.add_argument("--model", default="deepseek-v4-pro[1m]",
                        help="model for the LLM planner")

    p_run = sub.add_parser(
        "run", help="run a requirement through its task DAG in parallel worktrees")
    p_run.add_argument("requirement", help="natural-language requirement")
    p_run.add_argument("--dir", default=".", help="repository root")
    p_run.add_argument("--llm", action="store_true",
                       help="build the task DAG with the LLM planner "
                            "(default: deterministic planner)")
    p_run.add_argument("--jobs", type=int, default=2,
                       help="max parallel task worktrees (default: 2; 1 = sequential)")
    p_run.add_argument("--sandbox", choices=("auto", "on", "off"), default="auto",
                       help="command sandbox mode: auto = docker when available "
                            "else host (honestly reported); on = require docker; "
                            "off = host (default: auto)")
    p_run.add_argument("--task-id", default=None,
                       help="task id for the worktree/branch (default: auto)")
    p_run.add_argument("--model", default="deepseek-v4-pro[1m]")
    p_run.add_argument("--no-commit", action="store_true",
                       help="do not commit or merge the task changes")
    p_run.add_argument("--push", action="store_true",
                       help="push the task branch to origin after a "
                            "successful run")
    p_run.add_argument("--pr", action="store_true",
                       help="create a GitHub PR via gh (implies --push)")
    p_run.add_argument("--merge", action="store_true",
                       help="merge the created PR via gh (implies --pr)")
    p_run.add_argument("--squash", action="store_true",
                       help="merge method: squash (with --merge)")
    p_run.add_argument("--cleanup", action="store_true",
                       help="remove run worktrees whose work is merged/"
                            "pushed — never by force")

    p_issue = sub.add_parser(
        "issue", help="run a GitHub issue through the full flow: "
                      "fetch → requirement → run → push → PR")
    p_issue.add_argument("repo", nargs="?",
                         help="OWNER/REPO of the issue (needed without --json)")
    p_issue.add_argument("number", nargs="?", type=int,
                         help="issue number (needed without --json)")
    p_issue.add_argument("--json", default=None,
                         help="issue JSON file instead of `gh` (offline)")
    p_issue.add_argument("--dir", default=".", help="repository root")
    p_issue.add_argument("--llm", action="store_true",
                         help="build the task DAG with the LLM planner")
    p_issue.add_argument("--model", default="deepseek-v4-pro[1m]")
    p_issue.add_argument("--jobs", type=int, default=2,
                         help="max parallel task worktrees (default: 2)")
    p_issue.add_argument("--sandbox", choices=("auto", "on", "off"),
                         default="auto", help="command sandbox mode")
    p_issue.add_argument("--no-push", action="store_true",
                         help="skip the git push (default: push)")
    p_issue.add_argument("--no-pr", action="store_true",
                         help="skip PR creation (default: create)")
    p_issue.add_argument("--merge", action="store_true",
                         help="merge the PR after creation via gh")
    p_issue.add_argument("--squash", action="store_true",
                         help="merge method: squash (with --merge)")
    p_issue.add_argument("--cleanup", action="store_true",
                         help="remove run worktrees whose work is merged/"
                              "pushed — never by force")

    p_graph = sub.add_parser("graph", help="print the dependency graph")
    p_graph.add_argument("dir", nargs="?", default=".")

    p_bench = sub.add_parser("benchmark", help="run the Phase 9 evaluation")
    p_bench.add_argument("--out", default=None,
                         help="directory for raw results (default: under .repopilot)")
    p_bench.add_argument("--semantic", choices=("auto", "local", "api", "lsa"),
                         default="auto",
                         help="semantic embedding backend for the retrieval runs "
                              "(recorded per run; default auto)")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _cmd_init(args)
        if args.command == "index":
            return _cmd_index(args)
        if args.command == "ask":
            return _cmd_ask(args)
        if args.command == "plan":
            return _cmd_plan(args)
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "issue":
            return _cmd_issue(args)
        if args.command == "graph":
            return _cmd_graph(args)
        if args.command == "benchmark":
            return _cmd_benchmark(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"repopilot: error: {e}", file=sys.stderr)
        return 1
    return 0


# ─── commands ──────────────────────────────────────────────────

def _require_git(root: Path) -> None:
    import subprocess
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=str(root), capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"{root} is not a git repository (repopilot init/run "
                         "need one)")


def _repopilot_dir(root: Path) -> Path:
    return root / ".repopilot"


def _cmd_init(args) -> int:
    from ..application import RepositoryService
    result = RepositoryService().initialize(args.dir)
    if not result.ok:
        raise SystemExit(result.error)
    print(f"repopilot initialized: {result.config_path.parent}")
    print(f"  config: {result.config_path}")
    return 0


def _cmd_index(args) -> int:
    from ..application import RepositoryService
    result = RepositoryService().index(args.dir, full=args.full)
    print(f"indexed {result.files} files, {result.symbols} symbols, "
          f"{result.imports} imports, {result.references} references "
          f"({result.elapsed_s:.1f}s)")
    if result.files_by_language:
        langs = ", ".join(
            f"{lang}={n} [{result.parser_kinds.get(lang, '')}]"
            for lang, n in sorted(result.files_by_language.items()))
        print(f"  languages: {langs}")
    print(f"  index: {result.note}")
    print(f"  saved: {result.db_path}")
    return 0


def _cmd_ask(args) -> int:
    from ..application import AskService
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    result = AskService().ask(
        args.dir, args.question, model=args.model,
        semantic=args.semantic, api_key=api_key, base_url=_llm_base_url())
    print(f"(index: {result.index_note})")
    print(f"(semantic backend: {result.semantic_label} — "
          f"{result.semantic_note})")
    print("--- retrieved context ---")
    print(result.context[:4000])
    if not result.has_answer:
        print("\n(no ANTHROPIC_API_KEY — showing retrieval context only)")
        return 0
    print("\n--- answer ---")
    print(result.answer)
    return 0


def _print_plan(plan) -> None:
    """Print a TaskDAG: id, role, title, dependencies, task count."""
    print("--- plan ---")
    tasks = getattr(plan, "tasks", None)
    if isinstance(tasks, dict):  # TaskDAG: id -> TaskNode
        for t in tasks.values():
            deps = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
            print(f"- {t.id} [{t.agent_role}]: {t.title}{deps}")
        print(f"{len(tasks)} task(s)")
    elif tasks is not None:
        for t in tasks:
            deps = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
            print(f"- {t.id} [{t.agent_role}]: {t.title}{deps}")
        print(f"{len(tasks)} task(s)")
    else:
        print(json.dumps(plan, indent=2, ensure_ascii=False, default=str))


def _cmd_plan(args) -> int:
    from ..application import PlanningService
    service = PlanningService()
    requirement = service.parse(args.requirement)
    print(f"requirement: {requirement.title} (kind={requirement.kind.value})")
    print(f"description: {requirement.description}")
    if requirement.related_files:
        print(f"related files: {requirement.related_files}")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if args.llm and not api_key:
        print("error: --llm needs ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    result = service.create_plan(
        Path(".").resolve(), requirement, llm=args.llm, model=args.model,
        api_key=api_key, base_url=_llm_base_url())
    _print_plan(result.plan)
    return 0


def _require_key(what: str) -> str | None:
    """The API key or an honest failure (run/issue need a real LLM)."""
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key:
        print(f"error: repopilot {what} needs ANTHROPIC_API_KEY "
              "(or ANTHROPIC_AUTH_TOKEN)", file=sys.stderr)
    return key


def _cmd_run(args) -> int:
    from ..application import PlanningService, RunService
    root = Path(args.dir).resolve()
    _require_git(root)
    api_key = _require_key("run")
    if api_key is None:
        return 1
    planner = PlanningService()
    requirement = planner.parse(args.requirement)
    print(f"requirement: {requirement.title} (kind={requirement.kind.value})")
    task_id = args.task_id or f"T{int(time.time()) % 100000}"
    plan = planner.create_plan(
        root, requirement, llm=args.llm, model=args.model,
        api_key=api_key, base_url=_llm_base_url()).plan
    _print_plan(plan)
    import asyncio
    result = asyncio.run(RunService().run(
        root, requirement, plan=plan, task_id=task_id,
        model=args.model, api_key=api_key, base_url=_llm_base_url(),
        jobs=args.jobs, sandbox=args.sandbox, commit=not args.no_commit,
        push=args.push, pr=args.pr, merge=args.merge,
        cleanup=args.cleanup, squash=args.squash))
    report = result.report
    print(report.summarize())
    if result.github is not None:
        print(result.github.summarize())
    if report.pr is not None and not (result.github and result.github.pr_url):
        print(f"  PR body: {result.pr_file}")
        print(f"  create it with: gh pr create --head {report.pr.head_branch} "
              f"--base {report.pr.base_branch} --title \"{report.pr.title}\" "
              f"--body-file {result.pr_file}")
    return 0 if result.success else 1


def _cmd_issue(args) -> int:
    """GitHub Issue → Requirement → Run → Push → PR — the doc's full
    flow. `gh` fetches the issue (or --json supplies it offline); push
    and PR creation default ON, merge/cleanup are opt-in flags."""
    from ..application import PlanningService, RunService
    from ..product.github import GhError, fetch_issue, issue_to_requirement
    root = Path(args.dir).resolve()
    _require_git(root)
    if args.json:
        try:
            issue = json.loads(Path(args.json).read_text())
        except OSError as e:
            print(f"error: cannot read issue JSON: {e}", file=sys.stderr)
            return 1
    else:
        if not args.repo or not args.number:
            print("error: repopilot issue needs OWNER/REPO and NUMBER "
                  "(or --json issue.json)", file=sys.stderr)
            return 1
        try:
            issue = fetch_issue(args.repo, args.number)
        except GhError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    requirement = issue_to_requirement(issue)
    print(f"issue #{issue.get('number', '?')}: {requirement.title} "
          f"(kind={requirement.kind.value})")
    api_key = _require_key("issue")
    if api_key is None:
        return 1
    task_id = f"I{issue.get('number', 'X')}-T{int(time.time()) % 100000}"
    planner = PlanningService()
    plan = planner.create_plan(
        root, requirement, llm=args.llm, model=args.model,
        api_key=api_key, base_url=_llm_base_url()).plan
    _print_plan(plan)
    import asyncio
    result = asyncio.run(RunService().run(
        root, requirement, plan=plan, task_id=task_id,
        model=args.model, api_key=api_key, base_url=_llm_base_url(),
        jobs=args.jobs, sandbox=args.sandbox, commit=True,
        push=not args.no_push, pr=not args.no_pr, merge=args.merge,
        cleanup=args.cleanup, squash=args.squash))
    report = result.report
    print(report.summarize())
    if result.github is not None:
        print(result.github.summarize())
    if report.pr is not None and not (result.github and result.github.pr_url):
        print(f"  PR body: {result.pr_file}")
        print(f"  create it with: gh pr create --head {report.pr.head_branch} "
              f"--base {report.pr.base_branch} --title \"{report.pr.title}\" "
              f"--body-file {result.pr_file}")
    return 0 if result.success else 1


def _cmd_graph(args) -> int:
    from ..application import GraphService
    result = GraphService().graph(args.dir)
    print(f"(index: {result.index_note})")
    print(f"modules: {len(result.modules)}  edges: {result.edges}")
    for module in result.modules:
        deps = result.dependencies.get(module, [])
        if deps:
            print(f"  {module} -> {', '.join(deps)}")
    return 0


def _cmd_benchmark(args) -> int:
    from ..application import BenchmarkService
    out = Path(args.out) if args.out else _repopilot_dir(Path(".").resolve()) / "benchmark"
    result = BenchmarkService().run_retrieval_benchmark(
        semantic=args.semantic, out_dir=out)
    print(f"(semantic backend: {result.semantic_label} — "
          f"{result.semantic_note})")
    print("retrieval benchmark (real runs, 24 tasks × 5 stacks):")
    for stack, a in sorted(result.by_stack.items()):
        print(f"  {stack:32} recall@5={a['recall@5']:.3f} "
              f"recall@10={a['recall@10']:.3f} MRR={a['mrr']:.3f} "
              f"topk_hit@5={a['topk_hit@5']:.3f}")
    print(f"raw results: {result.raw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
