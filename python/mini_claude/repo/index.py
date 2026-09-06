"""RepositoryIndex — the Phase 2 facade: scan + parse + graph + persistence,
with hash/mtime-driven incremental updates and the query API the later
retrieval phases build on (find_symbol, find_definition, imports, deps)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from .graph import DependencyGraph
from .parser import PythonParser
from .scanner import DEFAULT_MAX_FILE_SIZE, RepositoryScanner, path_to_module
from .store import SQLiteStore
from .symbols import FileRecord, ImportInfo, ParsedModule, Symbol, SymbolKind


@dataclass
class IndexBuildResult:
    files: int = 0
    symbols: int = 0
    imports: int = 0
    syntax_errors: int = 0
    parsed_files: int = 0      # files actually parsed this build (incremental)
    removed_files: int = 0
    elapsed_s: float = 0.0
    incremental: bool = False


class RepositoryIndex:
    """Parses and indexes one Python repository.

    build() re-parses only changed files (mtime/size pre-filter, sha256
    confirm) when a previous state exists — either in memory or restored
    from the SQLite store via load().
    """

    def __init__(
        self,
        root: str | Path,
        *,
        db_path: str | Path | None = None,
        ignore_dirs: set[str] | None = None,
        max_file_size: int | None = None,
        parser: PythonParser | None = None,
    ):
        self.root = Path(root).resolve()
        self.db_path = Path(db_path) if db_path is not None else None
        self.scanner = RepositoryScanner(
            self.root, ignore_dirs=ignore_dirs,
            max_file_size=max_file_size or DEFAULT_MAX_FILE_SIZE,
        )
        self.parser = parser or PythonParser()
        self.graph = DependencyGraph()

        self._files: dict[str, FileRecord] = {}          # path -> record
        self._symbols: dict[str, list[Symbol]] = {}      # path -> symbols
        self._imports: dict[str, list[ImportInfo]] = {}  # path -> imports
        self._store: SQLiteStore | None = None

    # ─── Build ───────────────────────────────────────────────

    def build(self, *, persist: bool = False) -> IndexBuildResult:
        """Full or incremental index build. Returns build statistics."""
        started = time.monotonic()
        result = IndexBuildResult(incremental=bool(self._files))

        current = self.scanner.scan()
        current_set = {str(p) for p in current}

        # Detect changed/added/deleted against the previous state.
        changed: list[str] = []
        for rel in current_set:
            prev = self._files.get(rel)
            if prev is None:
                changed.append(rel)
                continue
            try:
                stat = (self.root / rel).stat()
            except OSError:
                continue
            if stat.st_mtime != prev.mtime or stat.st_size != prev.size:
                if self._hash_file(rel) != prev.content_hash:
                    changed.append(rel)

        removed = [p for p in self._files if p not in current_set]
        for rel in removed:
            self._remove_file(rel)
        result.removed_files = len(removed)

        for rel in sorted(changed):
            self._parse_and_index(rel)
        result.parsed_files = len(changed)

        # Two-phase: imports resolve against the COMPLETE module map, so the
        # graph must be rebuilt after every file is parsed — building it
        # incrementally would miss edges to files parsed later in the order.
        self._rebuild_graph()

        result.files = len(self._files)
        result.symbols = sum(len(v) for v in self._symbols.values())
        result.imports = sum(len(v) for v in self._imports.values())
        result.syntax_errors = sum(
            1 for f in self._files.values() if f.has_syntax_error
        )
        result.elapsed_s = time.monotonic() - started

        if persist:
            self.save()
        return result

    def _parse_and_index(self, rel_path: str) -> None:
        """Parse one file and register its symbols/imports (replacing any
        previous entries for that path)."""
        path = self.root / rel_path
        module_name = path_to_module(rel_path)
        try:
            mod = self.parser.parse_file(path, module_name=module_name)
        except Exception:
            # Unreadable/binary file — keep the previous record if any.
            return

        self._remove_file(rel_path)
        record = FileRecord(
            path=rel_path, module_name=mod.module_name,
            content_hash=mod.content_hash,
            mtime=path.stat().st_mtime, size=path.stat().st_size,
            has_syntax_error=mod.has_syntax_error,
        )
        self._files[rel_path] = record
        self._symbols[rel_path] = mod.symbols
        self._imports[rel_path] = mod.imports

    def _remove_file(self, rel_path: str) -> None:
        self._files.pop(rel_path, None)
        self._symbols.pop(rel_path, None)
        self._imports.pop(rel_path, None)

    def _hash_file(self, rel_path: str) -> str:
        return hashlib.sha256((self.root / rel_path).read_bytes()).hexdigest()

    # ─── Queries ─────────────────────────────────────────────

    def find_symbol(self, name: str, kind: SymbolKind | str | None = None) -> list[Symbol]:
        """All symbols whose NAME matches (exact), optionally filtered by kind."""
        wanted = SymbolKind(kind) if kind is not None and not isinstance(kind, SymbolKind) else kind
        return sorted(
            (s for syms in self._symbols.values() for s in syms
             if s.name == name and (wanted is None or s.kind is wanted)),
            key=lambda s: (s.file_path, s.location.start_line),
        )

    def find_definition(self, qualified_name: str) -> Symbol | None:
        """Exact qualified-name lookup — the canonical definition of a symbol.
        Falls back to exact name match when a bare name is passed and it is
        unambiguous."""
        for syms in self._symbols.values():
            for s in syms:
                if s.qualified_name == qualified_name:
                    return s
        matches = self.find_symbol(qualified_name)
        return matches[0] if len(matches) == 1 else None

    def symbols_in_file(self, file_path: str) -> list[Symbol]:
        return list(self._symbols.get(file_path, []))

    def file_of_symbol(self, symbol: Symbol) -> str:
        return symbol.location.file_path

    def imports_of(self, file_path: str) -> list[ImportInfo]:
        return list(self._imports.get(file_path, []))

    def file_dependencies(self, file_path: str) -> list[str]:
        return self.graph.file_dependencies(file_path)

    def module_dependencies(self, module: str) -> list[str]:
        return self.graph.module_dependencies(module)

    def dependents(self, file_path: str) -> list[str]:
        return self.graph.dependents(file_path)

    def importers_of(self, module: str) -> list[str]:
        return self.graph.importers_of(module)

    def files(self) -> list[str]:
        return sorted(self._files)

    def modules(self) -> list[str]:
        return self.graph.modules()

    def file_of_module(self, module: str) -> str | None:
        return self.graph.file_of_module(module)

    def file_record(self, file_path: str) -> FileRecord | None:
        return self._files.get(file_path)

    # ─── Persistence ─────────────────────────────────────────

    def save(self, db_path: str | Path | None = None) -> None:
        target = Path(db_path) if db_path is not None else self.db_path
        if target is None:
            raise ValueError("no db_path configured — pass one to save() or the constructor")
        store = self._store if self._store is not None else SQLiteStore(target)
        try:
            store.save(str(self.root), self._files, self._symbols, self._imports)
            if self._store is None:
                self._store = store
        except Exception:
            if self._store is None:
                store.close()
            raise

    def load(self, db_path: str | Path | None = None) -> bool:
        """Restore a previously saved index. Returns False when none exists.
        After loading, build() runs incrementally against the restored state."""
        target = Path(db_path) if db_path is not None else self.db_path
        if target is None:
            raise ValueError("no db_path configured — pass one to load() or the constructor")
        store = self._store if self._store is not None else SQLiteStore(target)
        try:
            restored = store.load()
        except Exception:
            restored = None
        if self._store is None:
            self._store = store
        if restored is None:
            return False
        root_path, files, symbols, imports = restored
        if root_path != str(self.root):
            return False  # db belongs to a different root — start fresh
        self._files = files
        self._symbols = symbols
        self._imports = imports
        self._rebuild_graph()
        return True

    def _rebuild_graph(self) -> None:
        self.graph = DependencyGraph()
        for path, record in self._files.items():
            self.graph.add_file(path, record.module_name)
        for path, imps in self._imports.items():
            for imp in imps:
                self.graph.add_import(path, imp)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
