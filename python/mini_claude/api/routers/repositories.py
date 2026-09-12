"""Repository API (Web Phase 3) — the §31 endpoints over the
RepositoryRegistry + RepositoryService, all under the §11 envelope."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..errors import envelope, request_id
from ..schemas import (IndexOut, IndexRequest, IndexStatusOut, InitOut,
                       RepositoryCreate, RepositoryOut)
from ...application import RepositoryService
from ...application.registry import RegistryContext, RepositoryRegistry
from ...persistence.database import session_factory

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])


def get_registry(request: Request):
    return RepositoryRegistry(RegistryContext(
        workspace_root=request.app.state.settings.workspace_root,
        session_factory=session_factory(
            request.app.state.settings.db_url)))


@router.post("", status_code=201)
def create_repository(body: RepositoryCreate,
                      registry: RepositoryRegistry = Depends(get_registry),
                      session: Session = Depends(get_db),
                      request: Request = None) -> JSONResponse:
    repo = registry.register(body.path, session=session)
    return JSONResponse(status_code=201,
                        content=envelope(RepositoryOut(**repo.to_dict()).model_dump(),
                                         request_id=request_id(request)))


@router.get("")
def list_repositories(registry: RepositoryRegistry = Depends(get_registry),
                      session: Session = Depends(get_db),
                      request: Request = None) -> dict:
    rows = [RepositoryOut(**r.to_dict()).model_dump()
            for r in registry.list(session=session)]
    return envelope(rows, meta={"count": len(rows)},
                    request_id=request_id(request))


@router.get("/{repo_id}")
def get_repository(repo_id: str,
                   registry: RepositoryRegistry = Depends(get_registry),
                   session: Session = Depends(get_db),
                   request: Request = None) -> dict:
    repo = registry.get(repo_id, session=session)
    return envelope(RepositoryOut(**repo.to_dict()).model_dump(),
                    request_id=request_id(request))


@router.delete("/{repo_id}")
def delete_repository(repo_id: str,
                      registry: RepositoryRegistry = Depends(get_registry),
                      session: Session = Depends(get_db),
                      request: Request = None) -> dict:
    registry.remove(repo_id, session=session)
    return envelope({"deleted": repo_id},
                    request_id=request_id(request))


@router.post("/{repo_id}/initialize")
def initialize_repository(repo_id: str,
                          registry: RepositoryRegistry = Depends(get_registry),
                          session: Session = Depends(get_db),
                          request: Request = None) -> dict:
    path = registry.path_of(repo_id, session=session)
    result = RepositoryService().initialize(path)
    if result.ok:
        repo = registry.get(repo_id, session=session)
        from datetime import datetime, timezone
        repo.initialized_at = datetime.now(timezone.utc)
        session.commit()
    return envelope(InitOut(ok=result.ok,
                            config_path=str(result.config_path)
                            if result.config_path else None,
                            error=result.error).model_dump(),
                    request_id=request_id(request))


@router.post("/{repo_id}/index")
def index_repository(repo_id: str, body: IndexRequest | None = None,
                     registry: RepositoryRegistry = Depends(get_registry),
                     session: Session = Depends(get_db),
                     request: Request = None) -> dict:
    path = registry.path_of(repo_id, session=session)
    result = RepositoryService().index(path, full=bool(body and body.full))
    repo = registry.get(repo_id, session=session)
    from datetime import datetime, timezone
    repo.last_indexed_at = datetime.now(timezone.utc)
    repo.last_index_note = result.note
    repo.index_files = result.files
    repo.index_symbols = result.symbols
    repo.index_errors = result.syntax_errors
    session.commit()
    return envelope(IndexOut(
        files=result.files, symbols=result.symbols,
        imports=result.imports, references=result.references,
        syntax_errors=result.syntax_errors, elapsed_s=result.elapsed_s,
        note=result.note, files_by_language=result.files_by_language,
        parser_kinds=result.parser_kinds).model_dump(),
        request_id=request_id(request))


@router.get("/{repo_id}/index")
def get_index_status(repo_id: str,
                     registry: RepositoryRegistry = Depends(get_registry),
                     session: Session = Depends(get_db),
                     request: Request = None) -> dict:
    path = registry.path_of(repo_id, session=session)
    status = RepositoryService().index_status(path)
    return envelope(IndexStatusOut(
        exists=status.exists, size_bytes=status.size_bytes,
        saved_at=status.saved_at, files=status.files,
        symbols=status.symbols, imports=status.imports,
        references=status.references).model_dump(),
        request_id=request_id(request))
