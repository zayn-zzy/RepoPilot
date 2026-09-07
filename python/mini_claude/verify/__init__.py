"""Phase 7 — Verification Pipeline + Self-Repair.

A fail-fast verification chain (syntax → lint → typecheck → targeted →
unit → integration → regression → reviewer) whose commands are detected
from what the repository actually offers, plus a bounded self-repair
loop (max 3 attempts) that turns a VerificationFailure into a diagnosis,
a coder repair and a targeted re-test."""

from .detection import ToolDetection, detect_tools  # noqa: F401
from .failure import (  # noqa: F401
    StageResult,
    VerificationFailure,
    VerificationReport,
)
from .pipeline import VerificationPipeline  # noqa: F401
from .repair import RepairAttempt, RepairResult, SelfRepairEngine  # noqa: F401

__all__ = [
    "VerificationFailure",
    "StageResult",
    "VerificationReport",
    "ToolDetection",
    "detect_tools",
    "VerificationPipeline",
    "SelfRepairEngine",
    "RepairAttempt",
    "RepairResult",
]
