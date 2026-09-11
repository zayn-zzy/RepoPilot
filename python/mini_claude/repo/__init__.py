"""RepoPilot Repository Intelligence — multi-language scanning, tree-sitter
(+ regex fallback) symbol extraction, the import dependency graph, the
symbol-level call/reference graph, SQLite persistence, incremental index."""

from .graph import DependencyGraph, ReferenceGraph
from .index import IndexBuildResult, RepositoryIndex
from .languages import LANGUAGE_BY_EXT, language_of, make_parser
from .parser import PythonParser
from .scanner import RepositoryScanner, path_to_module
from .store import SQLiteStore
from .symbols import (
    FileRecord,
    ImportInfo,
    Location,
    ParsedModule,
    Reference,
    Symbol,
    SymbolKind,
)

__all__ = [
    "DependencyGraph",
    "ReferenceGraph",
    "FileRecord",
    "ImportInfo",
    "IndexBuildResult",
    "LANGUAGE_BY_EXT",
    "Location",
    "ParsedModule",
    "PythonParser",
    "Reference",
    "RepositoryIndex",
    "RepositoryScanner",
    "SQLiteStore",
    "Symbol",
    "SymbolKind",
    "language_of",
    "make_parser",
    "path_to_module",
]
