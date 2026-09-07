"""Planner — turns a Requirement into a validated TaskDAG.

Two engines:
- plan(): deterministic rule-based decomposition (no LLM) — every kind gets
  a sensible role-assigned task chain, always valid.
- plan_with_llm(): the LLM-driven planner (Planner Prompt below); the model
  returns a JSON task spec that is parsed, converted, and validated — any
  structural violation raises PlannerError (the caller may then fall back to
  the deterministic plan).

Both produce a legal DAG: the acceptance criterion of this phase."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from .dag import DAGError, TaskDAG
from .requirement import Requirement, RequirementKind
from .task import TaskBudget, TaskNode, TaskStatus

# async (system: str, user: str) -> str
LLMCall = Callable[[str, str], Awaitable[str]]

PLANNER_SYSTEM = """You are the RepoPilot Planner. Decompose the requirement into a task DAG.
Respond with ONLY a JSON object, no prose:

{
  "tasks": [
    {
      "id": "T001",
      "title": "short imperative title",
      "description": "what to do and why",
      "agent_role": "coder",            // one of: explorer, coder, tester, reviewer
      "dependencies": ["T000"],          // ids of tasks that must finish first
      "priority": 1,                     // higher runs first
      "files": ["path/to/file.py"],      // files the task touches
      "budget": {"max_cost_usd": 0.5, "max_turns": 20}   // optional
    }
  ]
}

Rules:
- ids are unique strings
- dependencies reference existing ids, no cycles
- a complex feature decomposes into parallel independent branches where possible
- the last task is a reviewer review that depends on everything else
- write tasks go to "coder", verification to "tester", read-only analysis to
  "explorer", final review to "reviewer"
"""


class PlannerError(Exception):
    pass


class Planner:
    def __init__(self, llm_call: LLMCall | None = None):
        self._llm_call = llm_call

    # ─── Deterministic planner ───────────────────────────────

    def plan(self, requirement: Requirement | str) -> TaskDAG:
        if isinstance(requirement, str):
            from .requirement import RequirementParser
            requirement = RequirementParser().parse(requirement)
        return self._deterministic_plan(requirement)

    def _deterministic_plan(self, req: Requirement) -> TaskDAG:
        """Rule-based decomposition — one chain per requirement kind."""
        files = list(req.related_files)
        kind = req.kind
        tasks: list[TaskNode] = []
        base = self._base_id(kind)

        def add(seq: str, title: str, role: str, description: str,
                deps: list[str] | None = None, f: list[str] | None = None) -> str:
            tasks.append(TaskNode(
                id=f"{base}-{seq}",
                title=title,
                description=description,
                agent_role=role,
                dependencies=list(deps or []),
                priority=0,
                files=list(f if f is not None else files),
            ))
            return tasks[-1].id

        if kind is RequirementKind.FEATURE:
            impl = add("1", "Implement the feature", "coder",
                       f"Implement: {req.title}. {req.description}")
            test = add("2", "Add/verify tests", "tester",
                       "Cover the new behavior with tests and run them.", [impl])
            add("3", "Review the change", "reviewer",
                "Review the implementation and test results.", [test])
        elif kind is RequirementKind.BUG:
            fix = add("1", "Fix the bug", "coder",
                      f"Fix: {req.title}. {req.description}")
            add("2", "Add a regression test", "tester",
                "Reproduce the bug in a test, then verify the fix.", [fix])
        elif kind is RequirementKind.REFACTOR:
            ref = add("1", "Refactor", "coder",
                      f"Refactor: {req.title}. {req.description}")
            add("2", "Run tests", "tester",
                "Verify behavior is unchanged by the refactor.", [ref])
        elif kind is RequirementKind.TEST:
            add("1", "Write tests", "tester",
                f"Add tests: {req.title}. {req.description}")
        else:  # DOCUMENTATION
            add("1", "Write documentation", "coder",
                f"Document: {req.title}. {req.description}")

        dag = TaskDAG(tasks)
        report = dag.validate()
        if not report.valid:
            raise PlannerError(f"deterministic plan invalid: {report.errors}")
        return dag

    @staticmethod
    def _base_id(kind: RequirementKind) -> str:
        return {
            RequirementKind.FEATURE: "T-F",
            RequirementKind.BUG: "T-B",
            RequirementKind.REFACTOR: "T-R",
            RequirementKind.TEST: "T-T",
            RequirementKind.DOCUMENTATION: "T-D",
        }[kind]

    # ─── LLM planner ─────────────────────────────────────────

    async def plan_with_llm(self, requirement: Requirement | str) -> TaskDAG:
        if self._llm_call is None:
            raise PlannerError("no llm_call configured")
        if isinstance(requirement, str):
            from .requirement import RequirementParser
            requirement = RequirementParser().parse(requirement)

        user = self._build_planning_message(requirement)
        raw = await self._llm_call(PLANNER_SYSTEM, user)
        spec = self._extract_json(raw)
        return self._dag_from_spec(spec)

    def _build_planning_message(self, req: Requirement) -> str:
        lines = [
            f"Requirement kind: {req.kind.value}",
            f"Title: {req.title}",
            f"Description:\n{req.description}",
        ]
        if req.related_files:
            lines.append(f"Related files: {', '.join(req.related_files)}")
        if req.frames:
            lines.append("Stack trace frames: " + "; ".join(
                f"{f.file}:{f.line} in {f.function}" for f in req.frames[:5]))
        if req.test_failure and req.test_failure.failed_tests:
            lines.append("Failed tests: " + ", ".join(req.test_failure.failed_tests))
        lines.append("\nReturn the JSON task DAG now.")
        return "\n".join(lines)

    @staticmethod
    def _extract_json(raw: str) -> dict:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise PlannerError(f"no JSON object found in planner output: {raw[:200]!r}")
        try:
            spec = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise PlannerError(f"invalid JSON from planner: {e}") from e
        if not isinstance(spec, dict) or not isinstance(spec.get("tasks"), list):
            raise PlannerError("planner JSON must contain a 'tasks' array")
        return spec

    def _dag_from_spec(self, spec: dict) -> TaskDAG:
        dag = TaskDAG()
        for i, t in enumerate(spec["tasks"]):
            if not isinstance(t, dict):
                raise PlannerError(f"task #{i} is not an object")
            try:
                budget_raw = t.get("budget") or {}
                node = TaskNode(
                    id=str(t.get("id") or f"T{i:03d}"),
                    title=str(t.get("title") or "untitled"),
                    description=str(t.get("description") or ""),
                    agent_role=str(t.get("agent_role") or "coder"),
                    dependencies=[str(d) for d in (t.get("dependencies") or [])],
                    priority=int(t.get("priority") or 0),
                    files=[str(f) for f in (t.get("files") or [])],
                    budget=TaskBudget(
                        max_cost_usd=budget_raw.get("max_cost_usd"),
                        max_turns=budget_raw.get("max_turns"),
                    ),
                )
                dag.add_task(node)
            except (ValueError, DAGError) as e:
                raise PlannerError(f"task #{i} invalid: {e}") from e
        report = dag.validate()
        if not report.valid:
            raise PlannerError(f"planner produced an invalid DAG: {report.errors}")
        return dag
