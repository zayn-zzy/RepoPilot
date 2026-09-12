"""RunExecutor — what the worker does with one job: turn a persisted
Run row into a real RepoPilot run, streaming every lifecycle event to
the publisher. This is the same RunService the CLI uses, plus the
Web hooks (event sink / cancel check) — one implementation (§2.1)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..application import ApplicationError, PlanningService, RunService
from ..application.llm import llm_base_url
from ..application.registry import RegistryContext, RepositoryRegistry
from ..events.publisher import EventPublisher
from ..events.schema import make_event
from ..persistence.models import Run

log = logging.getLogger("repopilot.worker")


class RunExecutor:
    def __init__(self, settings, session_factory, publisher: EventPublisher,
                 cancel_registry, run_factory=None):
        """``run_factory`` is the test seam: async (path, run_row,
        api_key, sink, cancel_check, plan) -> RunResult-like.
        Production uses the real RunService path (_default_run)."""
        self._settings = settings
        self._factory = session_factory
        self._publisher = publisher
        self._cancels = cancel_registry
        self._run_factory = run_factory or self._default_run

    def _registry(self) -> RepositoryRegistry:
        return RepositoryRegistry(RegistryContext(
            workspace_root=self._settings.workspace_root,
            session_factory=self._factory))

    def _sink(self, run_id: str):
        """The DagRunner's event_sink — bridges its worker threads onto
        the publisher's loop (thread-safe by contract)."""
        return (lambda event: self._publisher.sink(
            run_id, event["event_type"],
            task_id=event.get("task_id"), agent_id=event.get("agent_id"),
            status=event.get("status"), message=event.get("message"),
            payload=event.get("payload")))

    def _cancel_check(self, run_id: str):
        return lambda: self._cancels.is_cancelled(run_id)

    async def _default_run(self, path, run, api_key, sink, cancel_check, plan):
        """The production path: the same RunService the CLI uses, with
        the Web hooks attached (one implementation, §2.1)."""
        from ..planning import RequirementParser
        requirement = RequirementParser().parse(
            f"{run.requirement_title}\n{run.requirement_description}")
        return await RunService().run(
            path, requirement, plan=plan, task_id=run.id,
            model=run.model or "deepseek-v4-pro[1m]", api_key=api_key,
            base_url=llm_base_url(), jobs=run.jobs or 1,
            sandbox=run.sandbox or "auto", commit=run.commit_enabled,
            event_sink=sink, cancel_check=cancel_check)

    async def execute(self, run_id: str) -> None:
        session = self._factory()
        run: Run | None = None
        try:
            run = session.get(Run, run_id)
            if run is None:
                raise ApplicationError("RUN_NOT_FOUND", f"run {run_id!r} not found")
            if run.status == "cancelled":
                return  # cancelled while queued — nothing to run
            path = self._registry().path_of(run.repository_id, session=session)
            await self._publisher.publish(make_event(
                run_id, "run.started", message=f"repository {run.repository_id}"))

            # plan (the worker plans from the stored requirement — the
            # deterministic planner, like the CLI default)
            await self._publisher.publish(make_event(
                run_id, "plan.started"))
            plan = PlanningService().create_plan(
                path, text=f"{run.requirement_title}\n"
                           f"{run.requirement_description}").plan
            await self._publisher.publish(make_event(
                run_id, "plan.completed",
                payload={"nodes": len(getattr(plan, "tasks", {}) or {})}))

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.worktree_task_id = run_id
            session.commit()

            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY") or \
                os.environ.get("ANTHROPIC_AUTH_TOKEN")
            if not api_key:
                raise ApplicationError(
                    "API_KEY_REQUIRED",
                    "the worker needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN)")

            result = await self._run_factory(
                path, run, api_key, self._sink(run_id),
                self._cancel_check(run_id), plan)
            report = result.report

            # final file.changed events + the row summary
            if report.diff is not None:
                for f in report.diff.all_files:
                    await self._publisher.publish(make_event(
                        run_id, "file.changed", payload={"path": f}))
            cancelled = self._cancels.is_cancelled(run_id)
            run.status = ("cancelled" if cancelled
                          else ("completed" if report.success else "failed"))
            run.finished_at = datetime.now(timezone.utc)
            run.verification_json = json.dumps(report.final_verification or {})
            run.diff_json = json.dumps(
                {"files": report.diff.all_files}
                if report.diff is not None else {})
            run.cost_usd = sum((o.cost_usd or 0) for o in report.outcomes)
            if report.pr is not None:
                run.pr_title = report.pr.title
            run.error = report.note or None
            session.commit()
            await self._publisher.publish(make_event(
                run_id,
                "run.cancelled" if cancelled
                else ("run.completed" if report.success else "run.failed"),
                message=(run.error or "")[:500],
                payload={"status": run.status}))
        except Exception as e:
            log.exception("run %s failed", run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.error = str(e)
                session.commit()
            await self._publisher.publish(make_event(
                run_id, "run.failed", message=str(e)[:500]))
            raise
        finally:
            session.close()
