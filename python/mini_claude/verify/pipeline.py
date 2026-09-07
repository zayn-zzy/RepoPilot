"""VerificationPipeline — the fail-fast verification chain.

    Code Change → Syntax → Lint → Type → Targeted → Unit → Integration
                → Regression → Reviewer

Every stage runs the command actually available in the repository
(detected by real probing — never assumed): pytest vs unittest, ruff vs
flake8 vs pyflakes, mypy vs pyright. Unavailable tools are recorded as
skipped with the reason; the first failing stage stops the chain and
its StageResult carries the unified VerificationFailure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ..planning import RequirementParser
from .detection import ToolDetection, detect_tools
from .failure import STAGE_ORDER, StageResult, VerificationReport

# Directories that are never scanned or tested (mirrors the Phase 2
# scanner's ignore list).
_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "worktrees",
                 "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache"}

TEST_FILE_PATTERNS = ("test_", "_test.py")


def _is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")


def _is_integration(path: Path) -> bool:
    """Integration tests = tests under integration-named paths/files."""
    return "integration" in path.name.lower() or any(
        "integration" in p.lower() for p in path.parts[:-1])


class VerificationPipeline:
    """Runs the verification chain once over a repository checkout.

    ``changed_files`` (repo-relative) is the Code Change input — stages
    scope their commands to it where meaningful; ``target_tests`` pins
    the targeted-test stage (the self-repair loop passes the failed test
    ids here). ``reviewer`` is the final human-quality gate hook."""

    COMMAND_TIMEOUT = 300.0

    def __init__(self, root: str | Path, *,
                 tools: ToolDetection | None = None,
                 changed_files: list[str] | None = None,
                 target_tests: list[str] | None = None,
                 reviewer: Callable[[VerificationReport], str] | None = None):
        self.root = Path(root).resolve()
        self.tools = tools or detect_tools(self.root)
        self.changed_files = [f for f in (changed_files or []) if f.strip()]
        self.target_tests = [t for t in (target_tests or []) if t.strip()]
        self.reviewer = reviewer

    # ─── file discovery ────────────────────────────────────────

    def _scan_py_files(self) -> list[Path]:
        return [p for p in self.root.rglob("*.py")
                if not any(part in _IGNORED_DIRS for part in p.relative_to(self.root).parts)]

    def _test_files(self) -> list[Path]:
        return sorted(p for p in self._scan_py_files()
                      if _is_test_file(p) and "pytest" not in str(p))

    def _scope(self, files: list[Path]) -> list[str]:
        """Scope a stage to the changed files, falling back to the whole
        repository when the change set is unknown."""
        changed = [self.root / f for f in self.changed_files if (self.root / f).is_file()]
        picked = [p for p in files if p in changed] if changed else files
        return [str(p.relative_to(self.root)) for p in picked]

    def _map_target_tests(self) -> list[str]:
        """Derive targeted test files from the changed source files:
        pkg/utils.py → test_utils.py / utils_test.py anywhere."""
        if self.target_tests:
            return self.target_tests
        targets = []
        for f in self.changed_files:
            p = self.root / f
            if not p.is_file() or not p.suffix == ".py":
                continue
            if _is_test_file(p):
                targets.append(f)
                continue
            stem = p.stem
            for t in self._test_files():
                rel = str(t.relative_to(self.root))
                if t.stem in (f"test_{stem}", f"{stem}_test"):
                    targets.append(rel)
        return sorted(set(targets))

    # ─── stage execution ───────────────────────────────────────

    def run(self) -> VerificationReport:
        report = VerificationReport(root=self.root)
        report.selected_tools = self._selection_record()
        for stage in STAGE_ORDER:
            result = self._run_stage(stage, report)
            report.stages.append(result)
            if result.status == "failed":
                break  # fail-fast: no further stages run
        return report

    def _selection_record(self) -> dict[str, str]:
        t = self.tools
        return {
            "python": t.python,
            "syntax": f"{Path(t.python).name} -m py_compile",
            "lint": t.lint_tool or "skipped: ruff/flake8/pyflakes not detected",
            "typecheck": t.type_tool or "skipped: mypy/pyright not detected",
            "test_runner": t.test_runner or "skipped: no test runner detected",
            "targeted_test": "pytest <mapped test files>"
                             if t.test_runner == "pytest" else "unittest <modules>",
            "reviewer": "callable hook" if self.reviewer else "skipped: no reviewer configured",
        }

    def _run_stage(self, stage: str, report: VerificationReport) -> StageResult:
        if stage == "syntax":
            return self._stage_syntax()
        if stage == "lint":
            return self._stage_lint()
        if stage == "typecheck":
            return self._stage_typecheck()
        if stage == "targeted_test":
            return self._stage_targeted_test()
        if stage == "unit_test":
            return self._stage_unit_test()
        if stage == "integration_test":
            return self._stage_integration_test()
        if stage == "regression_test":
            return self._stage_regression_test()
        return self._stage_reviewer(report)

    def _stage_syntax(self) -> StageResult:
        files = self._scope(self._scan_py_files())
        if not files:
            return StageResult(stage="syntax", status="passed",
                               command="(no python files)", detail="no .py files to compile")
        command = f"{Path(self.tools.python).name} -m py_compile {' '.join(files)}"
        p = self._run_cmd([self.tools.python, "-m", "py_compile", *files])
        return StageResult(stage="syntax", status=self._status(p),
                           command=command, exit_code=p.returncode,
                           stdout=p.stdout, stderr=p.stderr,
                           related_files=files)

    def _stage_lint(self) -> StageResult:
        tool = self.tools.lint_tool
        if tool is None:
            return StageResult(
                stage="lint", status="skipped", command="",
                detail="skipped: no lint tool detected (tried ruff/flake8/pyflakes)")
        files = self._scope(self._scan_py_files())
        if not files:
            return StageResult(stage="lint", status="passed",
                               command=f"{tool} (no files)", detail="no .py files to lint")
        argv = ([tool, "check"] if tool == "ruff" else [tool]) + files
        command = f"{tool} check {' '.join(files)}" if tool == "ruff" else f"{tool} {' '.join(files)}"
        p = self._run_cmd(argv)
        return StageResult(stage="lint", status=self._status(p),
                           command=command, exit_code=p.returncode,
                           stdout=p.stdout, stderr=p.stderr,
                           related_files=files)

    def _stage_typecheck(self) -> StageResult:
        tool = self.tools.type_tool
        if tool is None:
            return StageResult(
                stage="typecheck", status="skipped", command="",
                detail="skipped: no type checker detected (tried mypy/pyright)")
        files = self._scope(self._scan_py_files())
        if not files:
            return StageResult(stage="typecheck", status="passed",
                               command=f"{tool} (no files)", detail="no .py files to check")
        argv = ([self.tools.python, "-m", tool] if tool == "mypy" else [tool]) + files
        command = " ".join(argv)
        p = self._run_cmd(argv)
        return StageResult(stage="typecheck", status=self._status(p),
                           command=command, exit_code=p.returncode,
                           stdout=p.stdout, stderr=p.stderr,
                           related_files=files)

    def _stage_targeted_test(self) -> StageResult:
        targets = self._map_target_tests()
        if not targets:
            return StageResult(
                stage="targeted_test", status="skipped", command="",
                detail="skipped: no test file maps to the changed files and no target_tests given")
        return self._run_tests("targeted_test", targets)

    def _stage_unit_test(self) -> StageResult:
        all_tests = self._test_files()
        unit = sorted(p for p in all_tests if not _is_integration(p))
        if not unit:
            return StageResult(
                stage="unit_test", status="skipped", command="",
                detail="skipped: no unit test files found")
        return self._run_tests("unit_test", [str(p.relative_to(self.root)) for p in unit])

    def _stage_integration_test(self) -> StageResult:
        integration = sorted(p for p in self._test_files() if _is_integration(p))
        if not integration:
            return StageResult(
                stage="integration_test", status="skipped", command="",
                detail="skipped: no integration tests in this repository")
        return self._run_tests(
            "integration_test", [str(p.relative_to(self.root)) for p in integration])

    def _stage_regression_test(self) -> StageResult:
        return self._run_tests("regression_test", [])  # empty = whole suite

    def _run_tests(self, stage: str, files: list[str]) -> StageResult:
        runner = self.tools.test_runner
        if runner is None:
            return StageResult(
                stage=stage, status="skipped", command="",
                detail="skipped: no test runner detected (tried pytest, unittest)")
        if runner == "pytest":
            argv = [self.tools.python, "-m", "pytest", "-q", *files]
            command = " ".join(argv)
            p = self._run_cmd(argv)
            failed_tests = self._parse_failed_tests(p) if p.returncode != 0 else []
            # The change under verification is what the repair loop must
            # retrieve — related_files = the change + what was tested.
            related = list(dict.fromkeys(self.changed_files + files))
            return StageResult(stage=stage, status=self._status(p),
                               command=command, exit_code=p.returncode,
                               stdout=p.stdout, stderr=p.stderr,
                               failed_tests=failed_tests,
                               related_files=related)
        return self._run_unittest_discover(stage, files)

    def _run_unittest_discover(self, stage: str, files: list[str]) -> StageResult:
        """unittest fallback: discover per directory (module loading of
        non-package test dirs is unreliable, discover is not)."""
        specs: list[tuple[str, str]] = []  # (start dir, -p pattern)
        if files:
            for f in files:
                p = Path(f)
                specs.append((str(p.parent) if str(p.parent) != "." else ".", p.name))
        else:  # whole suite: one discover per directory per name pattern
            by_dir: dict[str, list[str]] = {}
            for t in self._test_files():
                rel = t.relative_to(self.root)
                by_dir.setdefault(str(rel.parent) if str(rel.parent) != "." else ".",
                                  []).append(t.name)
            for d, names in sorted(by_dir.items()):
                if any(n.startswith("test_") for n in names):
                    specs.append((d, "test_*.py"))
                if any(n.endswith("_test.py") for n in names):
                    specs.append((d, "*_test.py"))
        if not specs:
            return StageResult(
                stage=stage, status="skipped", command="",
                detail="skipped: no test files found for unittest discovery")
        commands, outputs = [], []
        for d, pattern in specs:
            argv = [self.tools.python, "-m", "unittest", "discover",
                    "-s", d, "-p", pattern]
            commands.append(" ".join(argv))
            p = self._run_cmd(argv)
            outputs.append(p)
            if p.returncode != 0:
                out = "\n\n".join(f"$ {' '.join(a)}\n{r.stdout}\n{r.stderr}"
                                  for a, r in zip(commands, outputs))
                return StageResult(
                    stage=stage, status="failed", command="; ".join(commands),
                    exit_code=p.returncode, stdout=out, stderr=p.stderr,
                    failed_tests=self._parse_failed_tests(p),
                    related_files=list(dict.fromkeys(self.changed_files + files)))
        return StageResult(stage=stage, status="passed",
                           command="; ".join(commands),
                           stdout="\n".join(r.stdout for r in outputs if r.stdout),
                           related_files=files)

    def _stage_reviewer(self, report: VerificationReport) -> StageResult:
        if self.reviewer is None:
            return StageResult(
                stage="reviewer", status="skipped", command="",
                detail="skipped: no reviewer configured")
        note = self.reviewer(report)  # the hook sees the stages run so far
        return StageResult(stage="reviewer", status="passed",
                           command="reviewer hook", detail=str(note)[:400])

    # ─── helpers ───────────────────────────────────────────────

    def _run_cmd(self, argv: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, cwd=str(self.root), capture_output=True, text=True,
                timeout=self.COMMAND_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # The command itself could not run — record it as a real
            # failure with the error, never crash the pipeline.
            return subprocess.CompletedProcess(argv, 1, "", f"{type(e).__name__}: {e}")

    @staticmethod
    def _status(p: subprocess.CompletedProcess) -> str:
        return "passed" if p.returncode == 0 else "failed"

    @staticmethod
    def _parse_failed_tests(p: subprocess.CompletedProcess) -> list[str]:
        """Reuse the Phase 4 test-failure parser for structured failed
        test ids (pytest FAILED lines, etc.)."""
        failure = RequirementParser().parse_test_failure(p.stdout + "\n" + p.stderr)
        return failure.failed_tests
