"""Repository scanner — discovers source files of every language the repo
understands (Python, JavaScript/TypeScript via tree-sitter; C-like and
scripting languages via the regex fallback), applying the ignore rules
(VCS/env/build dirs, vendored/generated code, large binaries) and mapping
paths to dotted module names."""

from __future__ import annotations

from pathlib import Path

from .languages import LANGUAGE_BY_EXT, language_of, path_to_module  # noqa: F401

# Directory names that are never scanned, wherever they appear.
IGNORED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    "target",
    "__pycache__",
    "vendor",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "generated",
}

# Filename markers for generated code (e.g. *_pb2.py, generated_code.py).
GENERATED_MARKERS = ("generated", "auto_generated", "_pb2")

DEFAULT_MAX_FILE_SIZE = 1_000_000  # 1 MB — anything bigger is a binary/blob


class RepositoryScanner:
    def __init__(
        self,
        root: str | Path,
        *,
        ignore_dirs: set[str] | None = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ):
        self.root = Path(root).resolve()
        self.ignore_dirs = IGNORED_DIRS | (ignore_dirs or set())
        self.max_file_size = max_file_size

    def scan(self) -> list[Path]:
        """Return sorted repo-relative paths of all scannable source files
        (every language in LANGUAGE_BY_EXT)."""
        files: list[Path] = []
        for dirpath, dirnames, filenames in self._walk():
            for name in sorted(filenames):
                path = dirpath / name
                if not self._is_source(path):
                    continue
                files.append(path.relative_to(self.root))
        return sorted(files)

    def scan_by_language(self) -> dict[str, list[Path]]:
        """Repo-relative paths grouped by language — the multi-language
        breakdown the index reports."""
        by_lang: dict[str, list[Path]] = {}
        for p in self.scan():
            by_lang.setdefault(language_of(p), []).append(p)
        return by_lang

    def _walk(self):
        # os.walk with in-place pruning keeps us out of ignored dirs entirely
        # (a Path.rglob + filter would still stat everything under .git).
        import os

        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = Path(dirpath).relative_to(self.root)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in self.ignore_dirs and not d.startswith(".")
            )
            yield Path(dirpath), dirnames, filenames

    def _is_source(self, path: Path) -> bool:
        if path.suffix.lower() not in LANGUAGE_BY_EXT:
            return False
        if any(marker in path.name for marker in GENERATED_MARKERS):
            return False
        try:
            if path.stat().st_size > self.max_file_size:
                return False
        except OSError:
            return False
        return True
