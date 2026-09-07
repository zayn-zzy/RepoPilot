"""TeamRunner — the five-role pipeline.

    Requirement → Planner → Explorer → Coder → Tester → Reviewer

Each role runs as an independent AgentRuntime (own prompt, ACL, context,
budget, trace). The ONLY channel between roles is the ArtifactMailbox:
every role publishes one structured artifact, and each role's prompt embeds
the artifacts produced before it. Agents never talk to each other directly
and never loop back — one pass, fixed order.

The pipeline runs with the process working directory set to the repository
root (file tools and shell commands are cwd-relative), restored afterwards.
Phase 6 replaces this coarse isolation with per-task git worktrees."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from ..planning import Requirement, RequirementParser
from ..runtime import AgentRuntime, ToolRegistry
from .artifact import ArtifactMailbox
from .roles import ROLE_NAMES, build_role_registry, build_role_runtime


@dataclass
class TeamConfig:
    model: str
    index: Any                    # RepositoryIndex over the target repo
    api_key: str | None = None
    api_base: str | None = None   # OpenAI-compatible backend (optional)
    anthropic_base_url: str | None = None
    permission_mode: str = "default"
    max_cost_usd: float | None = None
    max_turns: int | None = None
    interactive: bool = False


@dataclass
class RoleOutcome:
    role: str
    run: Any                      # RunResult from the AgentRuntime
    artifact: Any | None          # AgentArtifact | None (agent didn't publish)
    prompt: str                   # the prompt this role received


@dataclass
class TeamResult:
    requirement: Requirement
    outcomes: list[RoleOutcome] = field(default_factory=list)
    artifacts: list = field(default_factory=list)   # AgentArtifact list
    approved: bool | None = None

    @property
    def review(self):
        for a in reversed(self.artifacts):
            if a.kind == "review":
                return a
        return None


class TeamRunner:
    """Executes the five-role pipeline once per run(). Each run builds fresh
    role runtimes (fresh contexts and budgets); the mailbox is per-run."""

    def __init__(
        self,
        config: TeamConfig,
        *,
        after_build: Callable[[AgentRuntime], None] | None = None,
        registry: ToolRegistry | None = None,
    ):
        """`after_build` is invoked with each freshly built role runtime
        (tests inject scripted LLM clients here)."""
        self.config = config
        self._after_build = after_build
        self._registry = registry

    async def run(self, requirement: str | Requirement) -> TeamResult:
        if isinstance(requirement, str):
            requirement = RequirementParser().parse(requirement)
        mailbox = ArtifactMailbox()
        registry = self._registry or build_role_registry(self.config.index, mailbox)
        result = TeamResult(requirement=requirement)

        previous_cwd = os.getcwd()
        os.chdir(self.config.index.root)
        try:
            for role in ROLE_NAMES:
                outcome = await self._run_role(role, registry, mailbox, result)
                result.outcomes.append(outcome)
                if outcome.artifact is not None:
                    result.artifacts.append(outcome.artifact)
                # Planner / Explorer / Coder may stop the line early if their
                # artifact signals failure; the reviewer still gets the last word
                # only when the predecessors produced output.
                if role in ("planner", "explorer", "coder", "tester") and outcome.artifact is None:
                    break
        finally:
            os.chdir(previous_cwd)

        review = result.review
        # None = no verdict (the pipeline never reached a review);
        # True/False = the reviewer's explicit decision.
        result.approved = review.payload.get("approved") if review is not None else None
        return result

    async def _run_role(self, role: str, registry: ToolRegistry,
                        mailbox: ArtifactMailbox, result: TeamResult) -> RoleOutcome:
        runtime = build_role_runtime(
            role, self.config.index, mailbox,
            model=self.config.model,
            api_key=self.config.api_key,
            registry=registry,
            interactive=self.config.interactive,
            api_base=self.config.api_base,
            anthropic_base_url=self.config.anthropic_base_url,
            permission_mode=self.config.permission_mode,
            max_cost_usd=self.config.max_cost_usd,
            max_turns=self.config.max_turns,
        )
        if self._after_build is not None:
            self._after_build(runtime)

        prompt = self._build_prompt(role, result)
        run = await runtime.run(prompt)
        artifact = mailbox.latest(ARTIFACT_KINDS_BY_ROLE[role])
        await runtime.close()
        return RoleOutcome(role=role, run=run, artifact=artifact, prompt=prompt)

    def _build_prompt(self, role: str, result: TeamResult) -> str:
        req = result.requirement
        base = [
            f"Requirement (kind={req.kind.value}): {req.title}",
            req.description,
        ]
        if req.related_files:
            base.append(f"Related files: {', '.join(req.related_files)}")
        artifacts = self._artifact_context(result, role)
        if artifacts:
            base.append("\nArtifacts from earlier agents:\n" + artifacts)
        base.append(
            "\nComplete your role's work, then call publish_artifact with your structured result."
        )
        return "\n".join(base)

    @staticmethod
    def _artifact_context(result: TeamResult, role: str) -> str:
        visible = {
            "explorer": ("plan",),
            "coder": ("plan", "exploration"),
            "tester": ("plan", "code_change"),
            "reviewer": ("plan", "code_change", "test_report"),
        }.get(role, ())
        lines = []
        for a in result.artifacts:
            if a.kind in visible:
                lines.append(json.dumps(a.to_dict(), ensure_ascii=False))
        return "\n".join(lines)


# Which artifact kind each role publishes.
ARTIFACT_KINDS_BY_ROLE = {
    "planner": "plan",
    "explorer": "exploration",
    "coder": "code_change",
    "tester": "test_report",
    "reviewer": "review",
}
