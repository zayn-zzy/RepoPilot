"""BenchmarkService — the retrieval benchmark use case (Web Phase 1).

Wraps the real evaluation harness (24 tasks × 5 stacks, real runs) and
returns structured metrics — the WP10 Benchmark UI will chart exactly
this object; nothing is invented (§19)."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkResult:
    runs: list[dict] = field(default_factory=list)
    by_stack: dict[str, dict] = field(default_factory=dict)
    raw_path: Path | None = None
    semantic_label: str = ""
    semantic_note: str = ""


class BenchmarkService:
    def run_retrieval_benchmark(self, *,
                                semantic: str = "auto",
                                out_dir: str | Path | None = None,
                                tasks: list[Any] | None = None,
                                ) -> BenchmarkResult:
        """Real retrieval evaluation. ``tasks=None`` = the full 24-task
        suite (the CLI default); tests pass a subset. Results are saved
        as raw JSON (per-run rows + aggregates are recomputed from the
        rows — no fabricated numbers)."""
        from ..evaluation import aggregate, build_task_repos, retrieval_eval
        from ..retrieval import detect_embedding_backend
        with tempfile.TemporaryDirectory() as tmp:
            suite = build_task_repos(Path(tmp)) if tasks is None else tasks
            backend, note = detect_embedding_backend(prefer=semantic)
            runs = retrieval_eval(suite, semantic_backend=backend)
            by_stack: dict[str, dict] = {}
            for r in runs:
                by_stack.setdefault(r.baseline, []).append(r)
            aggregates = {stack: aggregate(rs)
                          for stack, rs in sorted(by_stack.items())}
            raw_path = None
            if out_dir is not None:
                import json
                out = Path(out_dir)
                out.mkdir(parents=True, exist_ok=True)
                raw_path = out / "retrieval_results.json"
                raw_path.write_text(
                    json.dumps([r.to_dict() for r in runs], indent=2))
            return BenchmarkResult(
                runs=[r.to_dict() for r in runs],
                by_stack=aggregates,
                raw_path=raw_path,
                semantic_label=backend.label,
                semantic_note=note,
            )
