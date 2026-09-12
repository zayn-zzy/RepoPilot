"""Ask API (Web Phase 4): POST /repositories/{id}/ask — structured
retrieval evidence (per-hit honest source scores + backend label) and
the optional LLM answer, via the AskService (one use case)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...application import AskService
from ...application.llm import llm_base_url
from ..dependencies import get_db
from ..errors import envelope, request_id
from ..schemas import AskHitOut, AskOut, AskRequest
from .repositories import get_registry

router = APIRouter(prefix="/api/v1", tags=["ask"])


@router.post("/repositories/{repo_id}/ask")
def ask_repository(repo_id: str,
                   body: AskRequest,
                   registry=Depends(get_registry),
                   session: Session = Depends(get_db),
                   request: Request = None) -> dict:
    path = registry.path_of(repo_id, session=session)
    api_key = os.environ.get("ANTHROPIC_API_KEY") or \
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
    result = AskService().ask(
        path, body.question, model=body.model, semantic=body.semantic,
        api_key=api_key, base_url=llm_base_url(), answer=body.answer)
    return envelope(AskOut(
        question=result.question,
        semantic_label=result.semantic_label,
        semantic_note=result.semantic_note,
        context=result.context,
        hits=[AskHitOut(file_path=h.file_path, score=h.score,
                        sources=h.sources).model_dump()
              for h in result.hits],
        answer=result.answer,
        has_answer=result.has_answer,
    ).model_dump(), request_id=request_id(request))
