"""PlanningService — requirement parsing + plan creation (Web Phase 1).

The plan is returned as a structured node/edge model (the same shape the
WP4 API and the Task DAG UI consume) — never a terminal string (§32)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ApplicationError


@dataclass
class PlanResult:
    requirement: Any                     # planning.Requirement
    plan: Any                            # planning.TaskDAG
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    planner: str = "deterministic"       # deterministic | llm


def plan_to_web(plan) -> tuple[list[dict], list[dict]]:
    """TaskDAG → {nodes, edges} for the API/React Flow. Node fields
    mirror TaskNode (id/role/title/description/dependencies/priority/
    files/budget); edges are dependency pairs."""
    nodes: list[dict] = []
    edges: list[dict] = []
    tasks = getattr(plan, "tasks", None)
    if not isinstance(tasks, dict):
        return nodes, edges
    for t in tasks.values():
        budget = getattr(t, "budget", None)
        nodes.append({
            "id": t.id,
            "agent_role": t.agent_role,
            "title": t.title,
            "description": getattr(t, "description", ""),
            "dependencies": list(t.dependencies),
            "priority": getattr(t, "priority", 0),
            "files": list(getattr(t, "files", []) or []),
            "budget": {
                "max_cost_usd": budget.max_cost_usd,
                "max_turns": budget.max_turns,
            } if budget is not None else None,
        })
        for dep in t.dependencies:
            edges.append({"source": dep, "target": t.id})
    return nodes, edges


class PlanningService:
    def parse(self, text: str):
        from ..planning import RequirementParser
        return RequirementParser().parse(text)

    def create_plan(self, path: str | Path, requirement=None, *,
                    text: str | None = None,
                    llm: bool = False,
                    model: str = "deepseek-v4-pro[1m]",
                    api_key: str | None = None,
                    base_url: str | None = None,
                    ) -> PlanResult:
        """Deterministic or LLM planner → structured PlanResult.
        ``path`` is kept for signature symmetry (repo-scoped plans come
        with the WP3 registry); the plan itself is repo-independent."""
        import asyncio
        from ..planning import Planner
        if requirement is None:
            requirement = self.parse(text or "")
        if llm:
            if not api_key:
                raise ApplicationError(
                    "API_KEY_REQUIRED", "LLM planning needs an API key")
            from .llm import make_llm_call
            planner = Planner(llm_call=make_llm_call(api_key, model))
            plan = asyncio.run(planner.plan_with_llm(requirement))
            planner_name = "llm"
        else:
            plan = Planner()._deterministic_plan(requirement)
            planner_name = "deterministic"
        nodes, edges = plan_to_web(plan)
        return PlanResult(requirement=requirement, plan=plan,
                          nodes=nodes, edges=edges, planner=planner_name)
