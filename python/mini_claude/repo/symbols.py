"""Data model for repository intelligence — symbols, imports, and parsed
module results. Plain dataclasses so they serialize cleanly to SQLite rows
and JSON for later retrieval phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"


@dataclass(frozen=True)
class Location:
    file_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Symbol:
    """One named definition extracted from a Python source file."""

    name: str
    kind: SymbolKind
    location: Location
    qualified_name: str          # e.g. "pkg.core.Processor.run"
    signature: str = ""          # "def run(self, x)" / "class Processor(Base)"
    docstring: str = ""

    @property
    def file_path(self) -> str:
        return self.location.file_path

    def is_method(self) -> bool:
        return self.kind is SymbolKind.METHOD


@dataclass(frozen=True)
class ImportInfo:
    """One import statement target, e.g. `from .sub import helper as h`."""

    file_path: str
    module: str                  # full dotted module, e.g. "pkg.sub.helper"
    symbol: str | None = None    # for `from m import x` (None for `import m`)
    alias: str | None = None
    level: int = 0               # 0 = absolute, >0 = relative dots
    lineno: int = 0

    def to_name(self) -> str:
        """The name this import binds locally."""
        if self.symbol:
            return self.alias or self.symbol
        return self.alias or self.module.split(".")[0]


@dataclass
class ParsedModule:
    """Everything the parser extracts from one file."""

    file_path: str
    module_name: str | None
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    has_syntax_error: bool = False
    content_hash: str = ""


@dataclass
class FileRecord:
    """Bookkeeping for one indexed file (drives incremental updates)."""

    path: str
    module_name: str | None
    content_hash: str
    mtime: float
    size: int
    has_syntax_error: bool = False
