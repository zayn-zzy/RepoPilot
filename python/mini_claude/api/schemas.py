"""API request/response schemas (pydantic v2) — the DTO source of truth
for /openapi.json (§24)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RepositoryCreate(BaseModel):
    path: str = Field(description="filesystem path inside the workspace root")


class RepositoryOut(BaseModel):
    id: str
    name: str
    path: str
    owner_id: str | None = None
    created_at: str | None = None
    initialized_at: str | None = None
    last_indexed_at: str | None = None
    last_index_note: str | None = None
    index_files: int = 0
    index_symbols: int = 0
    index_errors: int = 0


class InitOut(BaseModel):
    ok: bool
    config_path: str | None = None
    error: str = ""


class IndexRequest(BaseModel):
    full: bool = False


class IndexOut(BaseModel):
    files: int
    symbols: int
    imports: int
    references: int
    syntax_errors: int
    elapsed_s: float
    note: str
    files_by_language: dict[str, int] = {}
    parser_kinds: dict[str, str] = {}


class IndexStatusOut(BaseModel):
    exists: bool
    size_bytes: int = 0
    saved_at: float = 0.0
    files: int = 0
    symbols: int = 0
    imports: int = 0
    references: int = 0


class AskRequest(BaseModel):
    question: str
    model: str = "deepseek-v4-pro[1m]"
    semantic: str = "auto"      # auto | local | api | lsa
    answer: bool = True


class AskHitOut(BaseModel):
    file_path: str
    score: float
    sources: dict = {}


class AskOut(BaseModel):
    question: str
    semantic_label: str
    semantic_note: str
    context: str
    hits: list[AskHitOut] = []
    answer: str = ""
    has_answer: bool = False


class PlanCreateRequest(BaseModel):
    requirement: str
    model: str = "deepseek-v4-pro[1m]"
    llm: bool = False


class PlanOut(BaseModel):
    id: str
    repository_id: str | None = None
    requirement_title: str
    requirement_kind: str
    requirement_description: str
    model: str
    planner: str
    nodes: list[dict] = []
    edges: list[dict] = []
    created_at: str | None = None
