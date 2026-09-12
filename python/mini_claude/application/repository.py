"""RepositoryService — repository lifecycle use cases (Web Phase 1).

The CLI's init/index/graph-adjacent logic becomes a callable service:
no prints, structured results, core exceptions wrapped. The CLI and
(later) the FastAPI routers both call this (§2.1 — one implementation)."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ApplicationError

REPOPILOT_VERSION = "0.1.0"   # single source; product.cli re-imports this


@dataclass
class InitResult:
    root: Path
    config_path: Path | None = None
    ok: bool = True
    error: str = ""


@dataclass
class IndexResult:
    root: Path
    db_path: Path
    note: str
    files: int = 0
    symbols: int = 0
    imports: int = 0
    references: int = 0
    syntax_errors: int = 0
    elapsed_s: float = 0.0
    files_by_language: dict[str, int] = field(default_factory=dict)
    parser_kinds: dict[str, str] = field(default_factory=dict)


@dataclass
class IndexStatus:
    db_path: Path
    exists: bool
    size_bytes: int = 0
    saved_at: float = 0.0      # db file mtime (epoch)
    files: int = 0
    symbols: int = 0
    imports: int = 0
    references: int = 0


def _repopilot_dir(root: Path) -> Path:
    return root / ".repopilot"


class RepositoryService:
    """init / index / index-status — repo-keyed by path for now; the
    Web repository registry (WP3) will key by repo_id on top of this."""

    def initialize(self, path: str | Path) -> InitResult:
        root = Path(path).resolve()
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=str(root), capture_output=True, text=True)
        if p.returncode != 0:
            return InitResult(root=root, ok=False,
                              error=f"{root} is not a git repository "
                                    "(repopilot init needs one)")
        import json as _json
        cfg = _repopilot_dir(root)
        cfg.mkdir(exist_ok=True)
        config = {
            "repopilot_version": REPOPILOT_VERSION,
            "initialized_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(root),
        }
        config_path = cfg / "config.json"
        config_path.write_text(_json.dumps(config, indent=2))
        return InitResult(root=root, config_path=config_path)

    def index(self, path: str | Path, *, full: bool = False) -> IndexResult:
        from ..repo import RepositoryIndex
        root = Path(path).resolve()
        if not root.is_dir():
            raise ApplicationError(
                "REPOSITORY_NOT_FOUND", f"repository path does not exist: {root}")
        db = _repopilot_dir(root) / "index.db"
        index = RepositoryIndex(root)
        try:
            build, note = index.load_or_build(db, full=full)
        except Exception as e:
            raise ApplicationError("INDEX_FAILED", str(e)) from e
        return IndexResult(
            root=root, db_path=db, note=note,
            files=build.files, symbols=build.symbols, imports=build.imports,
            references=build.references, syntax_errors=build.syntax_errors,
            elapsed_s=build.elapsed_s,
            files_by_language=build.files_by_language,
            parser_kinds=build.parser_kinds,
        )

    def index_status(self, path: str | Path) -> IndexStatus:
        """Light status: db file metadata + SQL row counts (no parse,
        no graph rebuild) — the Dashboard's index card."""
        from ..repo.store import SQLiteStore
        root = Path(path).resolve()
        db = _repopilot_dir(root) / "index.db"
        if not db.is_file():
            return IndexStatus(db_path=db, exists=False)
        try:
            store = SQLiteStore(db)
            counts = store.counts()
            store.close()
        except Exception as e:
            raise ApplicationError("INDEX_STATUS_FAILED", str(e)) from e
        stat = db.stat()
        return IndexStatus(
            db_path=db, exists=True,
            size_bytes=stat.st_size, saved_at=stat.st_mtime,
            files=counts["files"], symbols=counts["symbols"],
            imports=counts["imports"], references=counts["symbol_refs"],
        )
