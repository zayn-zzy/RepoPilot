"""Capability detection — what verification tools does THIS repository
actually offer?

Every probe is a real subprocess invocation (--version etc.), so the
recorded selection is evidence, not assumption. Projects differ: the
spec forbids assuming ruff/mypy/pytest exist; whatever is (not) found is
recorded in ToolDetection.probe_log and in the report's selected_tools."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Probe = (argv) -> CompletedProcess
ProbeFn = Callable[[list[str]], subprocess.CompletedProcess]


def _real_probe(root: Path, timeout: float = 30.0) -> ProbeFn:
    def probe(argv: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, cwd=str(root), capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # A binary that exists but can't be executed (PermissionError on
            # a non-executable file, etc.) is simply "not available" — the
            # probe must never crash detection.
            return subprocess.CompletedProcess(argv, 1, "", f"{type(e).__name__}: {e}")
    return probe


@dataclass
class ToolDetection:
    """The repository's verified toolset."""

    python: str                        # interpreter for `python -m ...`
    test_runner: str | None            # "pytest" | "unittest" | None
    lint_tool: str | None              # "ruff" | "flake8" | "pyflakes" | None
    type_tool: str | None              # "mypy" | "pyright" | None
    probe_log: dict[str, str] = field(default_factory=dict)  # tool → real outcome


def detect_tools(root: str | Path, *, python: str | None = None,
                 probe: ProbeFn | None = None) -> ToolDetection:
    """Probe the real environment for the verification tools."""
    root = Path(root)
    probe = probe or _real_probe(root)
    python = python or sys.executable

    log: dict[str, str] = {}
    test_runner = None
    if probe([python, "-m", "pytest", "--version"]).returncode == 0:
        test_runner = "pytest"
        log["pytest"] = "detected (python -m pytest)"
    else:
        log["pytest"] = "not detected"
        if probe([python, "-m", "unittest", "--help"]).returncode == 0:
            test_runner = "unittest"
            log["unittest"] = "detected (stdlib fallback)"

    lint_tool = None
    for name, argv in (("ruff", ["ruff", "--version"]),
                       ("ruff-module", [python, "-m", "ruff", "--version"]),
                       ("flake8", ["flake8", "--version"]),
                       ("pyflakes", ["pyflakes", "--version"])):
        ok = probe(argv).returncode == 0
        log[name] = "detected" if ok else "not detected"
        if ok and lint_tool is None:
            lint_tool = name.split("-")[0] if name.startswith("ruff") else name

    type_tool = None
    for name, argv in (("mypy", ["mypy", "--version"]),
                       ("mypy-module", [python, "-m", "mypy", "--version"]),
                       ("pyright", ["pyright", "--version"])):
        ok = probe(argv).returncode == 0
        log[name] = "detected" if ok else "not detected"
        if ok and type_tool is None:
            type_tool = "mypy" if name.startswith("mypy") else name

    return ToolDetection(
        python=python,
        test_runner=test_runner,
        lint_tool=lint_tool,
        type_tool=type_tool,
        probe_log=log,
    )
