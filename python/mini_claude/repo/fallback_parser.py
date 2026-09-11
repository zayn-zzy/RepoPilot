"""Regex-based best-effort parser for languages without an installed
tree-sitter grammar (java, c/cpp, go, rust, csharp, ruby, php).

Symbols/imports/calls are approximations extracted from declarations,
import/include statements and call-shaped tokens — the parser kind is
recorded and surfaced, so these results are never presented as more
than they are. Class/function spans come from brace counting.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .symbols import ImportInfo, Location, ParsedModule, Reference, Symbol, SymbolKind

# ─── per-language patterns ────────────────────────────────────

_CLASS_PATTERNS: dict[str, re.Pattern] = {
    "java": re.compile(r"\b(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*(class|interface|enum)\s+([A-Za-z_]\w*)"),
    "c": re.compile(r"\b(struct|union|enum)\s+([A-Za-z_]\w*)"),
    "cpp": re.compile(r"\b(?:template\s*<[^>]*>\s*)?(class|struct|enum)\s+([A-Za-z_]\w*)"),
    "go": re.compile(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)"),
    "rust": re.compile(r"\b(struct|enum|trait|impl)\s+([A-Za-z_]\w*)"),
    "csharp": re.compile(r"\b(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|sealed\s+|static\s+|partial\s+)*(class|interface|struct|enum)\s+([A-Za-z_]\w*)"),
    "ruby": re.compile(r"\b(class|module)\s+([A-Za-z_]\w*)"),
    "php": re.compile(r"\b(?:abstract\s+|final\s+)?(class|interface|trait)\s+([A-Za-z_]\w*)"),
}

# Function-like definitions: [return type/modifiers] name ( — the leading
# type token is what separates definitions from call sites.
_FN_PATTERNS: dict[str, re.Pattern] = {
    "java": re.compile(r"(?<![\w.])(?:[\w.<>\[\]]+\s+)+(?!if|for|while|switch|catch|return|new|throw|synchronized\b)([A-Za-z_]\w*)\s*\("),
    "c": re.compile(r"(?<![\w.])(?:[\w\s*]+\s+)+(?!if|for|while|switch|return|sizeof\b)([A-Za-z_]\w*)\s*\("),
    "cpp": re.compile(r"(?<![\w.])(?:[\w:<>\s*&]+\s+)+(?!if|for|while|switch|catch|return|new|delete|sizeof\b)([A-Za-z_]\w*)\s*\("),
    # group 1 = receiver type (optional), group 2 = name — the NAME is
    # always the last group in every pattern.
    "go": re.compile(r"\bfunc\s+(?:\(\s*[\w.*]+\s+\*?([A-Za-z_]\w*)\s*\)\s+)?([A-Za-z_]\w*)\s*\("),
    "rust": re.compile(r"\bfn\s+([A-Za-z_]\w*)\s*[<(]"),
    "csharp": re.compile(r"(?<![\w.])(?:[\w.<>\[\]?\s]+\s+)+(?!if|for|while|foreach|switch|catch|return|new|throw|using\b)([A-Za-z_]\w*)\s*\("),
    "ruby": re.compile(r"\bdef\s+([A-Za-z_]\w*)"),
    "php": re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
}

_IMPORT_PATTERNS: dict[str, re.Pattern] = {
    "java": re.compile(r"\bimport\s+(?:static\s+)?([\w.]+)\s*;"),
    "c": re.compile(r'#include\s+[<"]([^>"]+)[>"]'),
    "cpp": re.compile(r'#include\s+[<"]([^>"]+)[>"]'),
    "rust": re.compile(r"\buse\s+([\w:]+(?:::\w+)*)"),
    "csharp": re.compile(r"\busing\s+([\w.]+)\s*;"),
    "ruby": re.compile(r"""\brequire(?:_relative)?\s+['"]([^'"]+)['"]"""),
    "php": re.compile(r"\buse\s+([\w\\]+)"),
}

_CALL_RE = re.compile(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")

# Call-shaped keywords that are not references.
_CALL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "do", "else", "elif",
    "when", "unless", "func", "fn", "def", "class", "import", "use",
    "require", "include", "package", "new", "throw", "sizeof", "foreach",
    "using", "module", "struct", "enum", "trait", "impl", "defer", "go",
    "select", "with", "assert", "print", "echo", "exit", "die", "raise",
}


def _brace_span(lines: list[str], start_line: int) -> int | None:
    """End line (1-based, inclusive) of the brace block opening anywhere
    on ``start_line``, or None when it never closes (header-only decl)."""
    depth = 0
    opened = False
    for j in range(start_line, len(lines)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    return j + 1
    return None


def _class_spans(language: str, lines: list[str]) -> list[tuple[str, int, int]]:
    """(name, start_line, end_line) for every class-like declaration —
    the name is the LAST capture group in every _CLASS_PATTERNS pattern."""
    spans = []
    for i, line in enumerate(lines):
        m = _CLASS_PATTERNS[language].search(line)
        if m is None:
            continue
        end = _brace_span(lines, i)
        spans.append((m.group(m.lastindex), i + 1, end if end is not None else i + 1))
    return spans


class FallbackParser:
    """Regex-based extraction. ``language`` selects the patterns; results
    are honest approximations (recorded as such by the index)."""

    def __init__(self, language: str):
        if language not in _CLASS_PATTERNS:
            raise ValueError(f"FallbackParser does not support {language!r}")
        self.language = language

    def parse_file(self, path: str | Path, module_name: str | None = None) -> ParsedModule:
        path = Path(path)
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        mod = ParsedModule(
            file_path=str(path),
            module_name=module_name,
            content_hash=hashlib.sha256(raw).hexdigest(),
        )
        lines = text.splitlines()
        class_spans = _class_spans(self.language, lines)
        fn_spans: dict[int, tuple[str, str]] = {}   # line -> (qname, end_line)
        qprefix = ([mod.module_name] if mod.module_name else [])

        # ── classes ──
        for name, start, end in class_spans:
            mod.symbols.append(Symbol(
                name=name, kind=SymbolKind.CLASS,
                location=Location(file_path=mod.file_path,
                                  start_line=start, end_line=end),
                qualified_name=".".join(qprefix + [name]),
                signature=f"{self.language}: {name}",
            ))

        # ── functions / methods ──
        fn_re = _FN_PATTERNS[self.language]
        for i, line in enumerate(lines):
            m = fn_re.search(line)
            if m is None:
                continue
            name = m.group(m.lastindex)   # the name is always the last group
            if name in _CALL_KEYWORDS:
                continue
            enclosing = next((c for c in class_spans if c[1] <= i + 1 <= c[2]), None)
            # Go binds methods by RECEIVER type, not brace position —
            # `func (h *Handler) Handle()` is a method even when the
            # `type Handler struct{}` is a one-liner.
            if (self.language == "go" and m.lastindex == 2 and m.group(1)
                    and enclosing is None):
                enclosing = next((c for c in class_spans if c[0] == m.group(1)),
                                 None)
            kind = SymbolKind.METHOD if enclosing is not None else SymbolKind.FUNCTION
            parts = qprefix + ([enclosing[0]] if enclosing else []) + [name]
            # Body span via brace counting; header-only declarations
            # (C prototypes, interface methods) keep their own line.
            end_line = _brace_span(lines, i) or i + 1
            mod.symbols.append(Symbol(
                name=name, kind=kind,
                location=Location(file_path=mod.file_path,
                                  start_line=i + 1, end_line=end_line),
                qualified_name=".".join(parts),
                signature=line.strip()[:160],
            ))
            fn_spans[i + 1] = (".".join(parts), end_line)

        # ── imports ──
        if self.language == "go":
            # Go imports appear only inside `import (...)` blocks or as
            # `import "pkg"` — a bare quoted-string pattern would also
            # match `return "ok"` and the like.
            text = "\n".join(lines)
            block_re = re.compile(r"\bimport\s*\((.*?)\)", re.DOTALL)
            single_re = re.compile(r'\bimport\s+"([^"]+)"')
            for m in block_re.finditer(text):
                lineno = text[:m.start()].count("\n") + 1
                for q in re.finditer(r'"([^"]+)"', m.group(1)):
                    mod.imports.append(ImportInfo(
                        file_path=mod.file_path, module=q.group(1),
                        lineno=lineno,
                    ))
            for m in single_re.finditer(text):
                mod.imports.append(ImportInfo(
                    file_path=mod.file_path, module=m.group(1),
                    lineno=text[:m.start()].count("\n") + 1,
                ))
        else:
            imp_re = _IMPORT_PATTERNS[self.language]
            for i, line in enumerate(lines):
                for m in imp_re.finditer(line):
                    mod.imports.append(ImportInfo(
                        file_path=mod.file_path, module=m.group(1),
                        lineno=i + 1,
                    ))

        # ── call references ──
        def_lines = {start - 1 for start in fn_spans}          # 0-based def lines
        def_lines |= {c[1] - 1 for c in class_spans}           # class header lines
        for i, line in enumerate(lines):
            if i in def_lines:
                continue  # `func f() {` / `class C {` headers are not calls
            for m in _CALL_RE.finditer(line):
                target = m.group(1)
                if target.split(".")[0] in _CALL_KEYWORDS or target in _CALL_KEYWORDS:
                    continue
                caller = ""
                for start_line, (qname, end_line) in fn_spans.items():
                    if start_line <= i + 1 <= end_line:
                        caller = qname
                        break
                if not caller:
                    caller = ".".join(qprefix)
                mod.references.append(Reference(
                    file_path=mod.file_path, caller=caller, target=target,
                    kind="call", lineno=i + 1,
                ))

        return mod
