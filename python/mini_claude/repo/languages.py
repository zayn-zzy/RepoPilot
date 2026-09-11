"""Language detection, language-aware module naming and the parser factory.

Tree-sitter grammars installed in this environment: python, javascript,
typescript (+ its tsx language). Every other extension falls back to the
regex-based FallbackParser — symbols/imports/calls are best-effort there
and the parser kind is recorded, never pretended.
"""

from __future__ import annotations

from pathlib import Path

# Extension → language. Only languages the repo claims to understand are
# listed; anything else is ignored by the scanner.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "tsx",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}

# Languages with a real tree-sitter parser (everything else is the
# regex fallback).
TREE_SITTER_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx"})


def language_of(path: str | Path) -> str | None:
    """The language of a file path, or None when the extension is not
    understood (the scanner then ignores the file)."""
    return LANGUAGE_BY_EXT.get(Path(path).suffix.lower())


def path_to_module(rel_path: str, language: str | None = None) -> str | None:
    """Map a repo-relative file path to its dotted module name.

    `pkg/sub/helper.py` → "pkg.sub.helper"; Python's `__init__.py` and
    Node's `index.js/ts` name their package (pkg/sub/__init__.py →
    "pkg.sub"). Returns None when the path isn't a valid module
    (non-identifier parts) — such files are indexed for symbols but
    excluded from module graphs."""
    lang = language or language_of(rel_path)
    if lang is None:
        return None
    parts = rel_path.split("/")
    if not parts or not parts[-1]:
        return None
    name = parts[-1]
    if lang == "python" and name == "__init__.py":
        parts = parts[:-1]
    elif name == "index.js" or name == "index.ts":
        parts = parts[:-1]          # Node convention: the package is the dir
    elif "." in name:
        parts[-1] = name.rsplit(".", 1)[0]
    for part in parts:
        if not part or not part.replace("_", "").isalnum() or part[0].isdigit():
            return None
    return ".".join(parts)


def make_parser(language: str):
    """A parser instance for the language (the index calls this once per
    language and reuses it)."""
    if language == "python":
        from .parser import PythonParser
        return PythonParser()
    if language in ("javascript", "typescript", "tsx"):
        from .js_parser import JsParser
        return JsParser(language)
    from .fallback_parser import FallbackParser
    return FallbackParser(language)
