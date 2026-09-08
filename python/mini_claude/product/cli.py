"""The `repopilot` CLI — the product surface of RepoPilot.

    repopilot init        prepare a repository (.repopilot/ config)
    repopilot index       build and persist the repository index
    repopilot ask         answer a question with retrieval + LLM context
    repopilot plan        parse a requirement and produce a plan
    repopilot run         run a requirement end-to-end in a worktree
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

REPOPILOT_VERSION = "0.1.0"


def main(argv: list[str] | None = None) -> int:
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

    p_ask = sub.add_parser("ask", help="answer a question using retrieval + LLM")
    p_ask.add_argument("question", help="the question about this repository")
    p_ask.add_argument("--dir", default=".", help="repository root")
    p_ask.add_argument("--model", default="deepseek-v4-pro[1m]")

    p_plan = sub.add_parser("plan", help="parse a requirement and produce a plan")
    p_plan.add_argument("requirement", help="natural-language requirement")
    p_plan.add_argument("--llm", action="store_true",
                        help="use the LLM planner (default: deterministic)")

    p_run = sub.add_parser("run", help="run a requirement end-to-end in a worktree")
    p_run.add_argument("requirement", help="natural-language requirement")
    p_run.add_argument("--dir", default=".", help="repository root")
    p_run.add_argument("--task-id", default=None,
                       help="task id for the worktree/branch (default: auto)")
    p_run.add_argument("--model", default="deepseek-v4-pro[1m]")
    p_run.add_argument("--no-commit", action="store_true",
                       help="do not commit the worktree changes")

    p_graph = sub.add_parser("graph", help="print the dependency graph")
    p_graph.add_argument("dir", nargs="?", default=".")

    p_bench = sub.add_parser("benchmark", help="run the Phase 9 evaluation")
    p_bench.add_argument("--out", default=None,
                         help="directory for raw results (default: under .repopilot)")

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
    root = Path(args.dir).resolve()
    _require_git(root)
    cfg = _repopilot_dir(root)
    cfg.mkdir(exist_ok=True)
    config = {
        "repopilot_version": REPOPILOT_VERSION,
        "initialized_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(root),
    }
    (cfg / "config.json").write_text(json.dumps(config, indent=2))
    print(f"repopilot initialized: {cfg}")
    print(f"  config: {cfg / 'config.json'}")
    return 0


def _cmd_index(args) -> int:
    from ..repo import RepositoryIndex
    root = Path(args.dir).resolve()
    index = RepositoryIndex(root)
    result = index.build()
    db = _repopilot_dir(root) / "index.db"
    db.parent.mkdir(exist_ok=True)
    index.save(db)
    print(f"indexed {result.files} files, {result.symbols} symbols, "
          f"{result.imports} imports ({result.elapsed_s:.1f}s)")
    print(f"  saved: {db}")
    return 0


def _cmd_ask(args) -> int:
    from ..retrieval import HybridRetriever
    from ..repo import RepositoryIndex
    root = Path(args.dir).resolve()
    index = RepositoryIndex(root)
    index.build()
    retriever = HybridRetriever(index)
    context = retriever.build_context(args.question, token_budget=3000, top_k=5)
    print("--- retrieved context ---")
    print(context[:4000])
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        print("\n(no ANTHROPIC_API_KEY — showing retrieval context only)")
        return 0
    from ..runtime import AgentRuntime, AgentConfig, build_default_registry
    runtime = AgentRuntime(AgentConfig(
        role="general", model=args.model, api_key=api_key,
        custom_system_prompt="You answer questions about a code repository "
                             "using the provided context.",
    ), registry=build_default_registry())
    import asyncio
    answer = asyncio.run(runtime.run(
        f"Context from the repository:\n{context}\n\nQuestion: {args.question}\n"
        "Answer concisely, citing file paths."))
    print("\n--- answer ---")
    print(answer.text)
    return 0


def _cmd_plan(args) -> int:
    from ..planning import Planner, RequirementParser
    requirement = RequirementParser().parse(args.requirement)
    print(f"requirement: {requirement.title} (kind={requirement.kind.value})")
    print(f"description: {requirement.description}")
    if requirement.related_files:
        print(f"related files: {requirement.related_files}")
    if args.llm:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not api_key:
            print("error: --llm needs ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        import asyncio
        planner = Planner(api_key=api_key,
                          base_url=os.environ.get("ANTHROPIC_BASE_URL"))
        plan = asyncio.run(planner.plan(requirement))
    else:
        planner = Planner()
        plan = planner._deterministic_plan(requirement)
    print("--- plan ---")
    print(json.dumps(plan.to_dict() if hasattr(plan, "to_dict") else plan,
                     indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_run(args) -> int:
    from ..product.orchestrator import run_requirement
    root = Path(args.dir).resolve()
    _require_git(root)
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        print("error: repopilot run needs ANTHROPIC_API_KEY "
              "(or ANTHROPIC_AUTH_TOKEN)", file=sys.stderr)
        return 1
    task_id = args.task_id or f"T{int(time.time()) % 100000}"
    import asyncio
    report = asyncio.run(run_requirement(
        root, args.requirement, task_id=task_id,
        model=args.model, api_key=api_key,
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        commit=not args.no_commit,
    ))
    print(report.summarize())
    if report.pr is not None:
        pr_file = _repopilot_dir(root) / f"pr-{task_id}.md"
        pr_file.write_text(report.pr.body)
        print(f"  PR body: {pr_file}")
        print(f"  create it with: gh pr create --head {report.pr.head_branch} "
              f"--base {report.pr.base_branch} --title \"{report.pr.title}\" "
              f"--body-file {pr_file}")
    return 0 if report.success else 1


def _cmd_graph(args) -> int:
    from ..repo import RepositoryIndex
    root = Path(args.dir).resolve()
    index = RepositoryIndex(root)
    index.build()
    graph = index.graph
    modules = graph.modules()
    edges = sum(len(index.module_dependencies(m)) for m in modules)
    print(f"modules: {len(modules)}  edges: {edges}")
    for module in modules:
        deps = index.module_dependencies(module)
        if deps:
            print(f"  {module} -> {', '.join(deps)}")
    return 0


def _cmd_benchmark(args) -> int:
    import tempfile
    from ..evaluation import TASK_SPECS, aggregate, build_task_repos, retrieval_eval
    with tempfile.TemporaryDirectory() as tmp:
        tasks = build_task_repos(Path(tmp))
        runs = retrieval_eval(tasks)
        out = Path(args.out) if args.out else _repopilot_dir(Path(".").resolve()) / "benchmark"
        out.mkdir(parents=True, exist_ok=True)
        raw = out / "retrieval_results.json"
        raw.write_text(json.dumps([r.to_dict() for r in runs], indent=2))
        print("retrieval benchmark (real runs, 24 tasks × 5 stacks):")
        by_stack = {}
        for r in runs:
            by_stack.setdefault(r.baseline, []).append(r)
        for stack, rs in sorted(by_stack.items()):
            a = aggregate(rs)
            print(f"  {stack:32} recall@5={a['recall@5']:.3f} "
                  f"recall@10={a['recall@10']:.3f} MRR={a['mrr']:.3f} "
                  f"topk_hit@5={a['topk_hit@5']:.3f}")
        print(f"raw results: {raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
