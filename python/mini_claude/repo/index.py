"""RepositoryIndex — the Phase 2 facade: scan + parse + graph + persistence,
with hash/mtime-driven incremental updates and the query API the later
retrieval phases build on (find_symbol, find_definition, imports, deps,
and the Phase 13 symbol-level call/reference graph).

Phase 13: multi-language. Every language in languages.LANGUAGE_BY_EXT is
scanned; python/javascript/typescript(+tsx) parse with tree-sitter, the
rest with the regex FallbackParser (honest best-effort)."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .graph import DependencyGraph, ReferenceGraph
from .languages import language_of, make_parser, path_to_module
from .scanner import DEFAULT_MAX_FILE_SIZE, RepositoryScanner
from .store import SQLiteStore
from .symbols import FileRecord, ImportInfo, Reference, Symbol, SymbolKind


@dataclass
class IndexBuildResult:
    files: int = 0
    symbols: int = 0
    imports: int = 0
    references: int = 0
    syntax_errors: int = 0
    parsed_files: int = 0      # files actually parsed this build (incremental)
    removed_files: int = 0
    elapsed_s: float = 0.0
    incremental: bool = False
    files_by_language: dict[str, int] = field(default_factory=dict)
    parser_kinds: dict[str, str] = field(default_factory=dict)  # language -> parser kind


class RepositoryIndex:
    """Parses and indexes one multi-language repository.

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
        parser=None,   # PythonParser for backward compatibility — an
                       # explicit override for the python language only
    ):
        self.root = Path(root).resolve()
        self.db_path = Path(db_path) if db_path is not None else None
        self.scanner = RepositoryScanner(
            self.root, ignore_dirs=ignore_dirs,
            max_file_size=max_file_size or DEFAULT_MAX_FILE_SIZE,
        )
        self.graph = DependencyGraph()
        self.ref_graph = ReferenceGraph()
        self._parsers: dict[str, object] = {}
        if parser is not None:
            self._parsers["python"] = parser

        self._files: dict[str, FileRecord] = {}          # path -> record
        self._symbols: dict[str, list[Symbol]] = {}      # path -> symbols
        self._imports: dict[str, list[ImportInfo]] = {}  # path -> imports
        self._references: dict[str, list[Reference]] = {}  # path -> references
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

        # Two-phase: imports and references resolve against the COMPLETE
        # module/symbol map, so the graphs must be rebuilt after every file
        # is parsed — building them incrementally would miss edges to files
        # parsed later in the order.
        self._rebuild_graph()

        result.files = len(self._files)
        result.symbols = sum(len(v) for v in self._symbols.values())
        result.imports = sum(len(v) for v in self._imports.values())
        result.references = sum(len(v) for v in self._references.values())
        result.syntax_errors = sum(
            1 for f in self._files.values() if f.has_syntax_error
        )
        result.elapsed_s = time.monotonic() - started
        by_lang = self.scanner.scan_by_language()
        result.files_by_language = {lang: len(files) for lang, files in by_lang.items()}
        result.parser_kinds = {
            lang: self._parser_kind(lang) for lang in result.files_by_language
        }

        if persist:
            self.save()
        return result

    def _parser_kind(self, language: str) -> str:
        parser = self._parsers.get(language) or make_parser(language)
        return type(parser).__name__

    def _parser_for(self, language: str):
        if language not in self._parsers:
            self._parsers[language] = make_parser(language)
        return self._parsers[language]

    def _parse_and_index(self, rel_path: str) -> None:
        """Parse one file and register its symbols/imports/references
        (replacing any previous entries for that path)."""
        lang = language_of(rel_path)
        if lang is None:
            return
        path = self.root / rel_path
        module_name = path_to_module(rel_path, lang)
        try:
            mod = self._parser_for(lang).parse_file(path, module_name=module_name)
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
        # References keep the parser's ABSOLUTE file paths (the same
        # contract as symbols/imports — the store normalizes them on
        # save). The reference GRAPH keys are repo-relative (built in
        # _rebuild_graph from the dict key).
        self._references[rel_path] = mod.references

    def _remove_file(self, rel_path: str) -> None:
        self._files.pop(rel_path, None)
        self._symbols.pop(rel_path, None)
        self._imports.pop(rel_path, None)
        self._references.pop(rel_path, None)

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

    # ─── References / call graph (Phase 13) ─────────────────

    def references_in(self, file_path: str) -> list[Reference]:
        return list(self._references.get(file_path, []))

    def callers_of(self, qualified_name: str) -> list[tuple[str, str, int]]:
        """Who references this symbol: (caller, kind, count)."""
        return self.ref_graph.callers_of(qualified_name)

    def callees_of(self, qualified_name: str) -> list[tuple[str, str, int]]:
        """What this symbol references: (target, kind, count)."""
        return self.ref_graph.callees_of(qualified_name)

    def unresolved_references(self) -> list[str]:
        """Reference targets that could not be resolved to a known symbol
        (external libraries, dynamic dispatch) — kept honestly."""
        return self.ref_graph.unresolved()

    def callees_in_file(self, file_path: str, limit: int = 15) -> list[tuple[str, int]]:
        """The top reference targets originating in this file."""
        return self.ref_graph.callees_in_file(file_path)[:limit]

    def callers_of_file(self, file_path: str, limit: int = 15) -> list[tuple[str, int]]:
        """Files that reference this file's symbols, most first."""
        return self.ref_graph.callers_of_file(file_path)[:limit]

    # ─── Persistence ─────────────────────────────────────────

    def save(self, db_path: str | Path | None = None) -> None:
        target = Path(db_path) if db_path is not None else self.db_path
        if target is None:
            raise ValueError("no db_path configured — pass one to save() or the constructor")
        store = self._store if self._store is not None else SQLiteStore(target)
        try:
            store.save(str(self.root), self._files, self._symbols, self._imports,
                       self._references)
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
        root_path, files, symbols, imports, references = restored
        if root_path != str(self.root):
            return False  # db belongs to a different root — start fresh
        self._files = files
        self._symbols = symbols
        self._imports = imports
        self._references = references
        self._rebuild_graph()
        return True

    def _rebuild_graph(self) -> None:
        self.graph = DependencyGraph()
        for path, record in self._files.items():
            self.graph.add_file(path, record.module_name)
        for path, imps in self._imports.items():
            for imp in imps:
                self.graph.add_import(path, imp)

        # Phase 13: the symbol-level call/reference graph.
        self._qnames: set[str] = set()
        qname_to_file: dict[str, str] = {}
        for path, syms in self._symbols.items():
            for s in syms:
                self._qnames.add(s.qualified_name)
                qname_to_file[s.qualified_name] = path
        self.ref_graph = ReferenceGraph()
        for path, refs in self._references.items():
            for ref in refs:
                # The graph is keyed by repo-relative paths (queries use
                # them); the stored Reference keeps its absolute path.
                graph_ref = Reference(file_path=path, caller=ref.caller,
                                      target=ref.target, kind=ref.kind,
                                      lineno=ref.lineno)
                resolved = self._resolve_reference(path, graph_ref)
                self.ref_graph.add_reference(
                    graph_ref, resolved, qname_to_file.get(resolved))

    # ─── reference resolution (best-effort, honest) ──────────

    def _resolve_reference(self, from_file: str, ref: Reference) -> str | None:
        """Resolve a reference target to a qualified symbol name:
        same-file symbols → import bindings → dotted paths → receiver
        (self/this) scope. Returns None when unresolvable — the target is
        then kept in the graph as an unresolved name."""
        target = ref.target
        symbols = self._symbols.get(from_file, [])
        if "." not in target:
            local = [s for s in symbols if s.name == target]
            if len(local) == 1:
                return local[0].qualified_name
            for imp in self._imports.get(from_file, []):
                if imp.to_name() == target:
                    q = self._resolve_import_symbol(imp)
                    if q:
                        return q
            return None

        head, rest = target.split(".", 1)
        if head in ("self", "this"):
            # a call on the receiver — resolve within the caller's class
            if ref.caller and "." in ref.caller:
                cls = ref.caller.rsplit(".", 1)[0]
                deeper = self._resolve_path_under(cls, rest)
                if deeper:
                    return deeper
            return None
        for imp in self._imports.get(from_file, []):
            if imp.to_name() == head:
                q = self._resolve_import_symbol(imp)
                if q:
                    deeper = self._resolve_path_under(q, rest)
                    if deeper:
                        return deeper
        for s in symbols:
            if s.name == head and s.kind is SymbolKind.CLASS:
                deeper = self._resolve_path_under(s.qualified_name, rest)
                if deeper:
                    return deeper
        # last resort: the dotted path relative to this file's module
        for s in symbols:
            if s.qualified_name.endswith("." + target):
                return s.qualified_name
        return None

    def _resolve_import_symbol(self, imp: ImportInfo) -> str | None:
        """The qualified name bound by one import, or None (external
        module / missing symbol)."""
        # ImportInfo.file_path is absolute (parser contract); the graph
        # keys are repo-relative.
        from_file = os.path.relpath(imp.file_path, self.root)
        module = self.graph.resolve_module(from_file, imp)
        if not module:
            return None
        file = self.graph.file_of_module(module)
        if file is None and (imp.module.startswith("./") or imp.module.startswith("../")):
            file = self._resolve_js_file(module)
        if file is None:
            return None
        mod_name = self.graph.module_of_file(file) or module
        if imp.symbol in (None, "", "*"):
            return mod_name
        if imp.symbol == "default":
            # JS default import — approximated by the module's only
            # top-level function/class, else the module itself.
            tops = [s for s in self._symbols.get(file, [])
                    if s.qualified_name.count(".") == mod_name.count(".") + 1
                    and s.qualified_name.startswith(mod_name + ".")]
            return tops[0].qualified_name if len(tops) == 1 else (mod_name or None)
        q = self._resolve_path_under(mod_name, imp.symbol)
        return q or mod_name

    def _resolve_js_file(self, module: str) -> str | None:
        """'src.utils.helper' (from './helper') → an actual indexed file
        path (helper.js/ts/tsx or helper/index.*)."""
        candidates = [module + ext for ext in (".js", ".ts", ".tsx", ".jsx")]
        candidates += [f"{module}/index{ext}" for ext in (".js", ".ts", ".tsx")]
        for c in candidates:
            if c in self._files:
                return c
        return None

    def _resolve_path_under(self, base_qname: str, dotted_path: str) -> str | None:
        expected = f"{base_qname}.{dotted_path}"
        return expected if expected in self._qnames else None

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
