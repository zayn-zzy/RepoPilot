"""RepositoryRegistry — the repository records behind the Web API
(Web Phase 3). The security boundary from §31 lives here: a registered
repository must exist, be a git repo, and resolve INSIDE the workspace
root (path traversal and symlink escapes are refused)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import Repository
from .errors import ApplicationError


@dataclass
class RegistryContext:
    """What the registry needs to run — injected by the API layer
    (workspace root from Settings, sessions from the db dependency)."""

    workspace_root: Path
    session_factory: object


class RepositoryRegistry:
    def __init__(self, context: RegistryContext):
        self._workspace = Path(context.workspace_root).resolve()
        self._factory = context.session_factory

    # ─── validation ──────────────────────────────────────────

    def _resolve_inside(self, path: str | Path) -> Path:
        """Resolve and require the path to be inside the workspace
        root. resolve() follows symlinks, so a symlink pointing outside
        the root is refused too."""
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            resolved = Path(path).expanduser()
        if not (resolved == self._workspace
                or self._workspace in resolved.parents):
            raise ApplicationError(
                "PATH_OUTSIDE_WORKSPACE",
                f"repository path {resolved} is outside the workspace "
                f"root {self._workspace}")
        return resolved

    def _validate_repo(self, resolved: Path) -> str:
        if not resolved.is_dir():
            raise ApplicationError("REPOSITORY_NOT_FOUND",
                                   f"repository path does not exist: {resolved}")
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=str(resolved), capture_output=True, text=True)
        if p.returncode != 0:
            raise ApplicationError(
                "NOT_A_GIT_REPOSITORY",
                f"{resolved} is not a git repository")
        return resolved.name or str(resolved)

    # ─── CRUD ────────────────────────────────────────────────

    def register(self, path: str | Path, *, session: Session | None = None) -> Repository:
        resolved = self._resolve_inside(path)
        name = self._validate_repo(resolved)
        own = session is None
        session = session or self._factory()
        try:
            existing = session.scalars(
                select(Repository).where(Repository.path == str(resolved))
            ).first()
            if existing is not None:
                if existing.is_active:
                    raise ApplicationError(
                        "ALREADY_EXISTS",
                        f"repository at {resolved} is already registered "
                        f"(id {existing.id})")
                existing.is_active = True
                existing.deleted_at = None
                existing.name = name
                session.commit()
                session.refresh(existing)
                return existing
            repo = Repository(name=name, path=str(resolved))
            session.add(repo)
            session.commit()
            session.refresh(repo)
            return repo
        except ApplicationError:
            raise
        finally:
            if own:
                session.close()

    def list(self, *, session: Session | None = None) -> list[Repository]:
        own = session is None
        session = session or self._factory()
        try:
            return list(session.scalars(
                select(Repository).where(Repository.is_active.is_(True))
                .order_by(Repository.created_at)))
        finally:
            if own:
                session.close()

    def get(self, repo_id: str, *, session: Session | None = None) -> Repository:
        own = session is None
        session = session or self._factory()
        try:
            repo = session.get(Repository, repo_id)
            if repo is None or not repo.is_active:
                raise ApplicationError(
                    "REPOSITORY_NOT_FOUND", f"repository {repo_id!r} not found")
            return repo
        finally:
            if own:
                session.close()

    def path_of(self, repo_id: str, *, session: Session | None = None) -> Path:
        return self._resolve_inside(self.get(repo_id, session=session).path)

    def remove(self, repo_id: str, *, session: Session | None = None) -> None:
        """Logical delete (§31: the record, not the files)."""
        own = session is None
        session = session or self._factory()
        try:
            repo = session.get(Repository, repo_id)
            if repo is None or not repo.is_active:
                raise ApplicationError(
                    "REPOSITORY_NOT_FOUND", f"repository {repo_id!r} not found")
            repo.is_active = False
            repo.deleted_at = datetime.now(timezone.utc)
            session.commit()
        finally:
            if own:
                session.close()
