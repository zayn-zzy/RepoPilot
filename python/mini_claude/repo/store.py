"""SQLite persistence — file records, symbols, imports and references,
plus index metadata. One database per repository index (default:
<root>/.repopilot/index.db is chosen by RepositoryIndex; this class is
db-path agnostic).

Symbol/Import/Reference locations are stored repo-relative so the database
survives a repo move; load() joins them back onto the saved root path.
The symbol_refs table (Phase 13; "references" is an SQLite keyword) is
additive: older databases without it gain the empty table on open."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .symbols import FileRecord, ImportInfo, Location, Reference, Symbol, SymbolKind

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS files (
    path             TEXT PRIMARY KEY,
    module_name      TEXT,
    content_hash     TEXT NOT NULL,
    mtime            REAL NOT NULL,
    size             INTEGER NOT NULL,
    has_syntax_error INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path      TEXT NOT NULL,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    signature      TEXT NOT NULL DEFAULT '',
    docstring      TEXT NOT NULL DEFAULT '',
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    module    TEXT NOT NULL,
    symbol    TEXT,
    alias     TEXT,
    level     INTEGER NOT NULL DEFAULT 0,
    lineno    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS symbol_refs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    caller    TEXT NOT NULL DEFAULT '',
    target    TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'call',
    lineno    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name, kind);
CREATE INDEX IF NOT EXISTS idx_symbols_qname ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_path);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module);
CREATE INDEX IF NOT EXISTS idx_references_file ON symbol_refs(file_path);
"""


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if self.db_path.parent and not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ─── Write ───────────────────────────────────────────────

    def save(self, root_path: str, files: dict[str, FileRecord],
             symbols: dict[str, list[Symbol]], imports: dict[str, list[ImportInfo]],
             references: dict[str, list[Reference]] | None = None) -> None:
        """Replace the whole index content (single transaction)."""
        with self._conn:
            self._conn.execute("DELETE FROM meta")
            self._conn.execute("DELETE FROM files")
            self._conn.execute("DELETE FROM symbols")
            self._conn.execute("DELETE FROM imports")
            self._conn.execute("DELETE FROM symbol_refs")
            self._conn.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [("schema_version", SCHEMA_VERSION), ("root_path", root_path)],
            )
            self._conn.executemany(
                "INSERT INTO files(path, module_name, content_hash, mtime, size, has_syntax_error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (f.path, f.module_name, f.content_hash, f.mtime, f.size,
                     1 if f.has_syntax_error else 0)
                    for f in files.values()
                ],
            )
            for path, syms in symbols.items():
                self._conn.executemany(
                    "INSERT INTO symbols(file_path, name, kind, qualified_name,"
                    " signature, docstring, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (os.path.relpath(s.file_path, root_path), s.name, s.kind.value,
                         s.qualified_name, s.signature, s.docstring,
                         s.location.start_line, s.location.end_line)
                        for s in syms
                    ],
                )
            for path, imps in imports.items():
                self._conn.executemany(
                    "INSERT INTO imports(file_path, module, symbol, alias, level, lineno)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (os.path.relpath(i.file_path, root_path), i.module, i.symbol,
                         i.alias, i.level, i.lineno)
                        for i in imps
                    ],
                )
            for path, refs in (references or {}).items():
                self._conn.executemany(
                    "INSERT INTO symbol_refs(file_path, caller, target, kind, lineno)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [
                        (os.path.relpath(r.file_path, root_path), r.caller,
                         r.target, r.kind, r.lineno)
                        for r in refs
                    ],
                )

    # ─── Read ────────────────────────────────────────────────

    def counts(self) -> dict[str, int]:
        """Light row counts (no object reconstruction) — the Web
        RepositoryService's index status uses this."""
        tables = ("files", "symbols", "imports", "symbol_refs")
        out: dict[str, int] = {}
        for table in tables:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()
            out[table] = int(row[0]) if row else 0
        return out

    def load(self, target_root: str | None = None) -> tuple[str, dict[str, FileRecord], dict[str, list[Symbol]], dict[str, list[ImportInfo]], dict[str, list[Reference]]] | None:
        """Restore the persisted index, or None when the db has no data.

        Locations are stored repo-relative and joined onto the SAVED root;
        ``target_root`` re-joins them onto a different checkout of the
        same repository instead (a task worktree adopting the main
        repo's index — Phase 16). The first tuple element is always the
        saved root, so callers can see where the db came from."""
        try:
            root = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'root_path'"
            ).fetchone()
            if root is None:
                return None
            root_path = root[0]
        except sqlite3.Error:
            return None
        join_root = target_root if target_root is not None else root_path

        files: dict[str, FileRecord] = {
            row[0]: FileRecord(
                path=row[0], module_name=row[1], content_hash=row[2],
                mtime=row[3], size=row[4], has_syntax_error=bool(row[5]),
            )
            for row in self._conn.execute("SELECT * FROM files")
        }
        symbols: dict[str, list[Symbol]] = {}
        for row in self._conn.execute(
            "SELECT file_path, name, kind, qualified_name, signature, docstring,"
            " start_line, end_line FROM symbols ORDER BY id"
        ):
            path, name, kind, qname, sig, doc, sl, el = row
            abs_path = os.path.join(join_root, path)
            symbols.setdefault(path, []).append(Symbol(
                name=name,
                kind=SymbolKind(kind),
                location=Location(file_path=abs_path, start_line=sl, end_line=el),
                qualified_name=qname,
                signature=sig,
                docstring=doc,
            ))
        imports: dict[str, list[ImportInfo]] = {}
        for row in self._conn.execute(
            "SELECT file_path, module, symbol, alias, level, lineno FROM imports ORDER BY id"
        ):
            path, module, symbol, alias, level, lineno = row
            abs_path = os.path.join(join_root, path)
            imports.setdefault(path, []).append(ImportInfo(
                file_path=abs_path, module=module, symbol=symbol, alias=alias,
                level=level, lineno=lineno,
            ))
        references: dict[str, list[Reference]] = {}
        for row in self._conn.execute(
            "SELECT file_path, caller, target, kind, lineno FROM symbol_refs ORDER BY id"
        ):
            path, caller, target, kind, lineno = row
            abs_path = os.path.join(join_root, path)
            references.setdefault(path, []).append(Reference(
                file_path=abs_path, caller=caller, target=target, kind=kind,
                lineno=lineno,
            ))
        return root_path, files, symbols, imports, references
