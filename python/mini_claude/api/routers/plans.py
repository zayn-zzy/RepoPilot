"""Plan API (Web Phase 4): POST /repositories/{id}/plans (structured
nodes/edges, persisted) + GET /plans/{id} — via PlanningService."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...application import PlanningService
from ...application.llm import llm_base_url
from ...persistence.models import Plan
from ..dependencies import get_db
from ..errors import envelope, request_id
from ..schemas import PlanCreateRequest, PlanOut
from .repositories import get_registry

router = APIRouter(prefix="/api/v1", tags=["plans"])


def _plan_out(plan: Plan) -> dict:
    return PlanOut(
        id=plan.id, repository_id=plan.repository_id,
        requirement_title=plan.requirement_title,
        requirement_kind=plan.requirement_kind,
        requirement_description=plan.requirement_description,
        model=plan.model, planner=plan.planner,
        nodes=json.loads(plan.nodes_json),
        edges=json.loads(plan.edges_json),
        created_at=plan.created_at.isoformat() if plan.created_at else None,
    ).model_dump()


@router.post("/repositories/{repo_id}/plans", status_code=201)
def create_plan(repo_id: str,
                body: PlanCreateRequest,
                registry=Depends(get_registry),
                session: Session = Depends(get_db),
                request: Request = None) -> JSONResponse:
    path = registry.path_of(repo_id, session=session)
    api_key = os.environ.get("ANTHROPIC_API_KEY") or \
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
    result = PlanningService().create_plan(
        path, text=body.requirement, llm=body.llm, model=body.model,
        api_key=api_key, base_url=llm_base_url())
    row = Plan(
        repository_id=repo_id,
        requirement_title=result.requirement.title or body.requirement,
        requirement_kind=result.requirement.kind.value,
        requirement_description=result.requirement.description,
        model=body.model, planner=result.planner,
        nodes_json=json.dumps(result.nodes, ensure_ascii=False),
        edges_json=json.dumps(result.edges),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return JSONResponse(status_code=201,
                        content=envelope(_plan_out(row),
                                         request_id=request_id(request)))


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str,
             session: Session = Depends(get_db),
             request: Request = None) -> dict:
    from ...application import ApplicationError
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise ApplicationError("PLAN_NOT_FOUND", f"plan {plan_id!r} not found")
    return envelope(_plan_out(plan), request_id=request_id(request))
