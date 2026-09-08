"""The unified failure/report model for the verification pipeline.

One VerificationFailure shape for every stage, per the spec:

    stage, command, exit_code, stdout, stderr, failed_tests, related_files
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Pipeline stages in execution order (the doc's chain, with the reviewer
# as the final gate).
STAGE_ORDER = (
    "syntax",
    "lint",
    "typecheck",
    "targeted_test",
    "unit_test",
    "integration_test",
    "regression_test",
    "reviewer",
)


@dataclass
class VerificationFailure:
    """One failed stage — the input of the self-repair loop."""

    stage: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    failed_tests: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)

    def summary(self, tail: int = 1200) -> str:
        """A compact Failure-Summarizer text for the repair prompt."""
        lines = [
            f"Stage: {self.stage}",
            f"Command: {self.command}",
            f"Exit code: {self.exit_code}",
        ]
        if self.failed_tests:
            lines.append(f"Failed tests: {', '.join(self.failed_tests)}")
        if self.related_files:
            lines.append(f"Related files: {', '.join(self.related_files)}")
        if self.stdout:
            lines.append(f"Stdout:\n{self.stdout[-tail:]}")
        if self.stderr:
            lines.append(f"Stderr:\n{self.stderr[-tail:]}")
        return "\n".join(lines)


@dataclass
class StageResult:
    """One stage's real outcome. ``status`` is passed / failed / skipped
    (skipped = the repository doesn't offer the tool for this stage —
    the skip reason is recorded in ``detail``)."""

    stage: str
    status: str                     # "passed" | "failed" | "skipped"
    command: str = ""               # the command that was selected and run
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    failed_tests: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    detail: str = ""                # skip reason / notes
    sandbox: str = ""               # "" = no sandbox configured; else the
                                    # honest record of where it ran:
                                    # "docker" | "host (reason)" | "blocked"

    @property
    def failure(self) -> VerificationFailure | None:
        if self.status != "failed":
            return None
        return VerificationFailure(
            stage=self.stage,
            command=self.command,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            failed_tests=self.failed_tests,
            related_files=self.related_files,
        )


@dataclass
class VerificationReport:
    """The outcome of one pipeline run — every stage, real commands,
    and which tools were ultimately selected (or skipped and why)."""

    root: Path
    stages: list[StageResult] = field(default_factory=list)
    selected_tools: dict[str, str] = field(default_factory=dict)
    # stage → human-readable selection record, e.g.
    # {"lint": "skipped: ruff/flake8/pyflakes not detected"}

    @property
    def passed(self) -> bool:
        return all(s.status != "failed" for s in self.stages)

    @property
    def first_failure(self) -> VerificationFailure | None:
        for s in self.stages:
            if s.failure is not None:
                return s.failure
        return None

    def stage(self, name: str) -> StageResult | None:
        for s in self.stages:
            if s.stage == name:
                return s
        return None
