"""Requirement schema and parser — classifies a requirement (bug / feature /
refactor / test / documentation), extracts structure from GitHub Issue-like
bodies, stack traces, and test-failure output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class RequirementKind(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCUMENTATION = "documentation"


# Keyword detectors — each hit adds to the kind's score; the highest-scoring
# kind wins (BUG beats ties, then FEATURE, REFACTOR, TEST, DOCUMENTATION).
_KIND_KEYWORDS: dict[RequirementKind, tuple[str, ...]] = {
    RequirementKind.BUG: (
        "bug", "fix", "broken", "crash", "exception", "traceback", "error",
        "regression", "defect", "修复", "错误", "崩溃", "缺陷", "故障",
    ),
    RequirementKind.FEATURE: (
        "feature", "add", "implement", "support", "enable", "introduce",
        "新增", "实现", "支持", "功能", "添加",
    ),
    RequirementKind.REFACTOR: (
        "refactor", "clean up", "cleanup", "simplify", "reorganize", "rename",
        "重构", "清理", "简化",
    ),
    RequirementKind.TEST: (
        "test", "coverage", "pytest", "unittest", "assertion",
        "测试", "覆盖率", "用例",
    ),
    RequirementKind.DOCUMENTATION: (
        "document", "doc", "readme", "docs", "comment", "文档", "说明", "注释",
    ),
}
_KIND_TIE_ORDER = (
    RequirementKind.BUG, RequirementKind.FEATURE, RequirementKind.REFACTOR,
    RequirementKind.TEST, RequirementKind.DOCUMENTATION,
)

_STACK_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')
_FAILED_TEST_RE = re.compile(r"FAILED\s+([\w./:\[\]-]+)")
_ISSUE_MARKERS = re.compile(
    r"^#{1,4}\s+\S|\*\*?(?:Description|Steps|Expected|Actual|Environment|描述|步骤|预期|实际|环境)\**\s*[:：]",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True)
class StackFrame:
    file: str
    line: int
    function: str


@dataclass(frozen=True)
class TestFailure:
    failed_tests: list[str] = field(default_factory=list)
    assertion_message: str = ""


@dataclass
class Requirement:
    kind: RequirementKind
    title: str
    description: str
    source: str
    related_files: list[str] = field(default_factory=list)
    stack_trace: str | None = None
    frames: list[StackFrame] = field(default_factory=list)
    test_failure: TestFailure | None = None
    issue_like: bool = False


class RequirementParser:
    def parse(self, text: str, *, title: str | None = None) -> Requirement:
        """Parse a free-form requirement (or GitHub Issue body)."""
        issue_like = self.is_issue_like(text)
        if title is None:
            first_line = next((ln for ln in text.splitlines() if ln.strip()), "").strip().lstrip("# ")
            title = first_line[:120] or "Untitled requirement"
        kind = self.detect_kind(text)
        req = Requirement(
            kind=kind,
            title=title,
            description=text.strip(),
            source=text,
            issue_like=issue_like,
        )
        if kind is RequirementKind.BUG:
            req.frames = self.parse_stack_trace(text)
            req.stack_trace = self._traceback_section(text)
            if req.frames:
                req.related_files = list(dict.fromkeys(f.file for f in req.frames))
        # Test-failure output matters for BUG and TEST kinds alike.
        failure = self.parse_test_failure(text)
        if failure.failed_tests or failure.assertion_message:
            req.test_failure = failure
        return req

    def parse_issue(self, title: str, body: str) -> Requirement:
        """GitHub Issue-like input: a title plus a markdown body."""
        return self.parse(f"{title}\n\n{body}", title=title.strip())

    def detect_kind(self, text: str) -> RequirementKind:
        lowered = text.lower()
        scores: dict[RequirementKind, int] = {}
        for kind, keywords in _KIND_KEYWORDS.items():
            scores[kind] = sum(lowered.count(kw) for kw in keywords)
        best = max(scores, key=lambda k: (scores[k], -_KIND_TIE_ORDER.index(k)))
        return best if scores[best] > 0 else RequirementKind.FEATURE

    def is_issue_like(self, text: str) -> bool:
        return bool(_ISSUE_MARKERS.search(text))

    def parse_stack_trace(self, text: str) -> list[StackFrame]:
        return [
            StackFrame(file=m.group(1), line=int(m.group(2)), function=m.group(3))
            for m in _STACK_FRAME_RE.finditer(text)
        ]

    def parse_test_failure(self, text: str) -> TestFailure:
        failed = list(dict.fromkeys(_FAILED_TEST_RE.findall(text)))
        assertion = ""
        m = re.search(r"AssertionError[:：]\s*(.+)", text)
        if m:
            assertion = m.group(1).strip()
        return TestFailure(failed_tests=failed, assertion_message=assertion)

    @staticmethod
    def _traceback_section(text: str) -> str | None:
        m = re.search(r"(Traceback \(most recent call last\):.*?)(?:\n\n(?!\s)|\Z)", text, re.DOTALL)
        return m.group(1) if m else None
