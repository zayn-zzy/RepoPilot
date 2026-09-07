"""SelfRepairEngine — the bounded repair loop.

    Verification Failure → Failure Summarizer → Root Cause Analysis
        → Retrieve Related Code → Coder Repair → Targeted Re-test

Each attempt builds a fresh coder AgentRuntime (the Phase 5 coder role:
write-capable ACL, its own context and budget) whose prompt contains
the failure summary, the related code retrieved from the repository
(index-backed when a RepositoryIndex is given) and the previous
attempts. The engine itself re-tests with a targeted pipeline run —
the coder never grades its own work. At most max_repair_attempts
(default 3, per the spec) attempts are made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..runtime import AgentRuntime, build_default_registry
from .failure import VerificationFailure, VerificationReport
from .pipeline import VerificationPipeline

RELATED_CODE_CAP = 4000  # chars per retrieved file


@dataclass
class RepairAttempt:
    attempt: int
    prompt: str                 # what the coder was given
    outcome_text: str           # the coder's final text (diagnosis/fix note)
    re_test: VerificationReport | None
    fixed: bool


@dataclass
class RepairResult:
    failure: VerificationFailure
    attempts: list[RepairAttempt] = field(default_factory=list)
    fixed: bool = False

    @property
    def final_report(self) -> VerificationReport | None:
        for attempt in reversed(self.attempts):
            if attempt.re_test is not None:
                return attempt.re_test
        return None


class SelfRepairEngine:
    """Drives the repair loop. LLM access reuses the Phase 5 coder role;
    ``after_build`` is invoked with each freshly built coder runtime
    (tests inject scripted LLM clients here)."""

    MAX_REPAIR_ATTEMPTS = 3

    def __init__(self, root: str | Path, *,
                 pipeline: VerificationPipeline | None = None,
                 index: Any | None = None,
                 model: str = "mock-model",
                 api_key: str | None = None,
                 api_base: str | None = None,
                 anthropic_base_url: str | None = None,
                 permission_mode: str = "acceptEdits",
                 max_cost_usd: float | None = None,
                 max_turns: int | None = None,
                 after_build: Callable[[AgentRuntime], None] | None = None,
                 max_repair_attempts: int = MAX_REPAIR_ATTEMPTS):
        self.root = Path(root).resolve()
        self.pipeline = pipeline or VerificationPipeline(self.root)
        self.index = index
        self.llm = dict(model=model, api_key=api_key, api_base=api_base,
                        anthropic_base_url=anthropic_base_url,
                        permission_mode=permission_mode,
                        max_cost_usd=max_cost_usd, max_turns=max_turns)
        self._after_build = after_build
        self.max_repair_attempts = max_repair_attempts

    async def repair(self, failure: VerificationFailure) -> RepairResult:
        """Run the loop until a targeted re-test passes or the attempt
        budget is exhausted."""
        result = RepairResult(failure=failure)
        for attempt_no in range(1, self.max_repair_attempts + 1):
            prompt = self._build_prompt(failure, attempt_no, result.attempts)
            runtime = self._build_coder()
            if self._after_build is not None:
                self._after_build(runtime)
            run = await runtime.run(prompt)
            outcome_text = (run.text or "").strip()
            await runtime.close()
            re_test = self._targeted_retest(failure)
            attempt = RepairAttempt(
                attempt=attempt_no, prompt=prompt, outcome_text=outcome_text,
                re_test=re_test, fixed=re_test.passed,
            )
            result.attempts.append(attempt)
            if attempt.fixed:
                result.fixed = True
                break
        return result

    # ─── the loop's steps ──────────────────────────────────────

    def _build_coder(self) -> AgentRuntime:
        """A fresh coder role per attempt (Phase 5 role + ACL reuse)."""
        from ..agents.artifact import ArtifactMailbox
        from ..agents.roles import build_role_runtime

        mailbox = ArtifactMailbox()
        registry = None
        if self.index is not None:
            from ..agents.roles import build_role_registry
            registry = build_role_registry(self.index, mailbox)
        return build_role_runtime(
            "coder", self.index or _FakeIndex(self.root), mailbox,
            model=self.llm["model"], api_key=self.llm["api_key"],
            registry=registry if registry is not None else build_default_registry(),
            api_base=self.llm["api_base"],
            anthropic_base_url=self.llm["anthropic_base_url"],
            permission_mode=self.llm["permission_mode"],
            max_cost_usd=self.llm["max_cost_usd"],
            max_turns=self.llm["max_turns"],
        )

    def _build_prompt(self, failure: VerificationFailure, attempt_no: int,
                      prior: list[RepairAttempt]) -> str:
        parts = [
            "You are the Coder agent inside a self-repair loop. A verification "
            f"stage FAILED (attempt {attempt_no} of {self.max_repair_attempts}).",
            "",
            "=== Failure summary ===",
            failure.summary(),
        ]
        related = self._retrieve_related_code(failure)
        if related:
            parts += ["", "=== Related code (retrieved from the repository) ===",
                      related]
        if prior:
            parts.append("\n=== Previous attempts (do not repeat them) ===")
            for a in prior:
                parts.append(f"Attempt {a.attempt}: {a.outcome_text[:800] or '(no output)'}")
        parts += [
            "",
            "Your job: 1) diagnose the ROOT CAUSE of the failure, 2) edit the "
            "minimal set of files to fix it. Keep changes small and focused.",
            "Do not run the test suite yourself — the verifier re-tests your "
            "changes automatically. When your edits are complete, end your turn "
            "with one short paragraph: the root cause and what you changed.",
        ]
        return "\n".join(parts)

    def _retrieve_related_code(self, failure: VerificationFailure) -> str:
        """Retrieve Related Code: the failed test files and the change's
        related files, plus index-backed dependency hints when available."""
        paths = []
        for test in failure.failed_tests:
            file = test.split("::", 1)[0]
            if file.endswith(".py"):
                paths.append(file)
        paths += [f for f in failure.related_files if f.endswith(".py")]
        paths = list(dict.fromkeys(paths))

        sections = []
        for rel in paths:
            p = self.root / rel
            if not p.is_file():
                sections.append(f"### {rel}\n(not found in the repository)")
                continue
            content = p.read_text(errors="replace")
            if len(content) > RELATED_CODE_CAP:
                content = content[:RELATED_CODE_CAP] + "\n... (truncated)"
            sections.append(f"### {rel}\n```python\n{content}\n```")
            if self.index is not None:
                try:
                    deps = self.index.file_dependencies(rel)
                    dependents = self.index.dependents(rel)
                except Exception:
                    deps, dependents = [], []
                if deps or dependents:
                    sections.append(f"(deps: {deps or '[]'} | dependents: {dependents or '[]'})")
        return "\n\n".join(sections)

    def _targeted_retest(self, failure: VerificationFailure) -> VerificationReport:
        """Targeted Re-test: a fresh pipeline run pinned to the failed
        tests and the changed files (the coder's edits are already in the
        working tree — the pipeline verifies them)."""
        return VerificationPipeline(
            self.root,
            tools=self.pipeline.tools,
            changed_files=failure.related_files or None,
            target_tests=failure.failed_tests or None,
            reviewer=self.pipeline.reviewer,
        ).run()


class _FakeIndex:
    """Minimal stand-in so the coder role's registry can be built without a
    Phase 2 index; only the root is used (no repo tools are registered)."""

    def __init__(self, root: Path):
        self.root = root
