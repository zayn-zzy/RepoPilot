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
    lint_module: bool = False          # detected via `python -m` (else PATH binary)
    type_module: bool = False          # detected via `python -m` (else PATH binary)
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

    # The MODULE form is probed first: the stage commands run with
    # `python -m <tool>` — a PATH binary from another environment (e.g.
    # a conda mypy next to a venv python) would probe green but fail at
    # run time. When only the binary exists, the recorded argv form says
    # so and the stage runs the binary as-is.
    lint_tool = None
    lint_module = False
    for name, argv, module in (("ruff-module", [python, "-m", "ruff", "--version"], True),
                               ("ruff", ["ruff", "--version"], False),
                               ("flake8-module", [python, "-m", "flake8", "--version"], True),
                               ("flake8", ["flake8", "--version"], False),
                               ("pyflakes", ["pyflakes", "--version"], False)):
        ok = probe(argv).returncode == 0
        log[name] = "detected" if ok else "not detected"
        if ok and lint_tool is None:
            lint_tool = name.split("-")[0]
            lint_module = module

    type_tool = None
    type_module = False
    for name, argv, module in (("mypy-module", [python, "-m", "mypy", "--version"], True),
                               ("mypy", ["mypy", "--version"], False),
                               ("pyright", ["pyright", "--version"], False)):
        ok = probe(argv).returncode == 0
        log[name] = "detected" if ok else "not detected"
        if ok and type_tool is None:
            type_tool = "mypy" if name.startswith("mypy") else name
            type_module = module

    return ToolDetection(
        python=python,
        test_runner=test_runner,
        lint_tool=lint_tool,
        type_tool=type_tool,
        lint_module=lint_module,
        type_module=type_module,
        probe_log=log,
    )
