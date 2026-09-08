"""Observability (doc §24) — every agent run is recorded.

Each record carries the run's structured events (LLM requests, tool
calls, tool results, verification, repair, final result) and the metric
set the benchmark consumes directly: LLM calls, input/output/cached
tokens, tool calls, tool latency, runtime, estimated cost, files
read/modified, tests executed, repair attempts, task success/failure.

Records are appended as JSONL (append-only, machine-readable, one line
per run) — the same shape the Phase 9 evaluation saved per run.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunRecord:
    """One agent run, doc §24 complete."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    task: str = ""
    agent: str = ""
    started_at: float = field(default_factory=time.time)
    # structured events
    llm_requests: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    verification: dict | None = None
    repair: dict | None = None
    final_result: dict | None = None
    # metrics (doc §24)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_call_count: int = 0
    tool_latency_s: float = 0.0
    runtime_s: float = 0.0
    estimated_cost_usd: float = 0.0
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tests_executed: int = 0
    repair_attempts: int = 0
    task_success: bool | None = None
    task_failure: bool | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "task": self.task, "agent": self.agent,
            "started_at": self.started_at,
            "llm_requests": self.llm_requests,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "verification": self.verification,
            "repair": self.repair,
            "final_result": self.final_result,
            "metrics": {
                "llm_calls": self.llm_calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens,
                "tool_calls": self.tool_call_count,
                "tool_latency_s": round(self.tool_latency_s, 3),
                "runtime_s": round(self.runtime_s, 3),
                "estimated_cost_usd": round(self.estimated_cost_usd, 6),
                "files_read": self.files_read,
                "files_modified": self.files_modified,
                "tests_executed": self.tests_executed,
                "repair_attempts": self.repair_attempts,
                "task_success": self.task_success,
                "task_failure": self.task_failure,
            },
        }


class RunLogger:
    """Append-only JSONL store (one record per line)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, record: RunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines()
                if line.strip()]


class RunRecorder:
    """Attaches to one AgentRuntime and collects its events + metrics
    (tool latency measured between the call and its result)."""

    def __init__(self, task: str = "", agent: str = ""):
        self.record = RunRecord(task=task, agent=agent)
        self._tool_start: dict[str, float] = {}

    def attach(self, runtime) -> None:
        def on_llm_request(data):
            self.record.llm_requests.append(dict(data))
            self.record.llm_calls += 1

        def on_tool_call(data):
            self.record.tool_calls.append(dict(data))
            self.record.tool_call_count += 1
            name = data.get("tool_name", "")
            inp = data.get("input") or {}
            self._tool_start[data.get("tool_use_id", name)] = time.monotonic()
            if name == "read_file":
                p = str(inp.get("file_path", ""))
                if p and p not in self.record.files_read:
                    self.record.files_read.append(p)
            elif name in ("write_file", "edit_file"):
                p = str(inp.get("file_path", ""))
                if p and p not in self.record.files_modified:
                    self.record.files_modified.append(p)

        def on_tool_result(data):
            self.record.tool_results.append(dict(data))
            key = data.get("tool_use_id", data.get("tool_name", ""))
            start = self._tool_start.pop(key, None)
            if start is not None:
                self.record.tool_latency_s += time.monotonic() - start

        runtime.events.on("llm_request", on_llm_request)
        runtime.events.on("tool_call", on_tool_call)
        runtime.events.on("tool_result", on_tool_result)

    def finish(self, run, *, verification: dict | None = None,
               repair: dict | None = None,
               final_result: dict | None = None) -> RunRecord:
        """Merge the RunResult's trace metrics and close the record."""
        metrics = run.trace.metrics()
        self.record.input_tokens = int(getattr(run, "tokens_in", 0)
                                       or metrics.get("input_tokens", 0))
        self.record.output_tokens = int(getattr(run, "tokens_out", 0)
                                        or metrics.get("output_tokens", 0))
        self.record.cached_tokens = int(getattr(run, "tokens_cache_read", 0))
        self.record.runtime_s = float(metrics.get("runtime_s") or 0.0)
        self.record.estimated_cost_usd = float(getattr(run, "cost_usd", 0.0)
                                               or metrics.get("cost_usd", 0.0))
        self.record.verification = verification
        self.record.repair = repair
        self.record.final_result = final_result
        if final_result is not None:
            self.record.task_success = bool(final_result.get("success"))
            self.record.task_failure = not bool(final_result.get("success"))
            self.record.tests_executed = int(final_result.get("tests_total", 0))
        if repair is not None:
            self.record.repair_attempts = int(repair.get("attempts", 0))
        return self.record
