#!/usr/bin/env python3
"""RepoPilot retrieval benchmark — compares four retrieval configurations
over the mini_claude corpus against hand-labeled relevance judgments:

    grep           plain regex grep ranked by match count (Baseline A)
    semantic       LSA cosine similarity only (Baseline B)
    hybrid         lexical (BM25) + semantic, RRF fused, reranked (Baseline C)
    hybrid+graph   + structural retrieval with 1/2-hop dependency expansion

Metrics: Recall@5, Recall@10, MRR, Hit@5, Hit@10 — computed per query, then
averaged. Raw per-query results are saved next to this file so every number
is reproducible. Deterministic: no randomness, no LLM.

Run: python tests/benchmark/retrieval_benchmark.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_PYTHON_DIR = _HERE.parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.repo import RepositoryIndex  # noqa: E402
from mini_claude.retrieval import HybridRetriever, grep_baseline  # noqa: E402
from mini_claude.retrieval.semantic import SemanticRetriever  # noqa: E402

CORPUS_ROOT = _PYTHON_DIR / "mini_claude"
RESULTS_PATH = _HERE.parent / "results" / "retrieval_benchmark.json"
TOPK = 10


def load_queries() -> list[dict]:
    return json.loads((_HERE.parent / "queries.json").read_text())["queries"]


def read_corpus(files: list[str]) -> dict[str, str]:
    return {
        rel: (CORPUS_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in files
    }


def metrics_for_query(retrieved: list[str], relevant: set[str]) -> dict:
    rel = relevant
    hits = [p for p in retrieved if p in rel]
    first_rank = next((i + 1 for i, p in enumerate(retrieved) if p in rel), None)
    return {
        "recall@5": len([p for p in retrieved[:5] if p in rel]) / max(1, len(rel)),
        "recall@10": len(hits) / max(1, len(rel)),
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "hit@5": 1.0 if any(p in rel for p in retrieved[:5]) else 0.0,
        "hit@10": 1.0 if hits else 0.0,
    }


def run_method(name: str, fn, queries: list[dict], relevant_map: dict) -> dict:
    per_query = {}
    for q in queries:
        t0 = time.monotonic()
        hits = fn(q["query"])
        elapsed = time.monotonic() - t0
        rel = relevant_map[q["id"]]
        m = metrics_for_query([h.file_path for h in hits], rel)
        per_query[q["id"]] = {
            "query": q["query"],
            "difficulty": q.get("difficulty"),
            "retrieved": [h.file_path for h in hits][:TOPK],
            "relevant": sorted(rel),
            "metrics": m,
            "ms": round(elapsed * 1000, 1),
        }
    agg = {
        metric: round(sum(pq["metrics"][metric] for pq in per_query.values()) / max(1, len(per_query)), 4)
        for metric in ("recall@5", "recall@10", "mrr", "hit@5", "hit@10")
    }
    by_difficulty = {}
    for difficulty in sorted({q.get("difficulty") for q in queries}):
        subset = [pq for pq in per_query.values() if pq["difficulty"] == difficulty]
        by_difficulty[difficulty] = {
            metric: round(sum(pq["metrics"][metric] for pq in subset) / max(1, len(subset)), 4)
            for metric in ("recall@5", "recall@10", "mrr")
        }
    return {"method": name, "aggregate": agg, "by_difficulty": by_difficulty, "per_query": per_query}


def main() -> None:
    queries = load_queries()
    relevant_map = {q["id"]: set(q["relevant_files"]) for q in queries}

    index = RepositoryIndex(CORPUS_ROOT)
    index.build()
    files = read_corpus(index.files())
    print(f"corpus: {len(files)} files, {sum(index.symbols_in_file(p) and 1 or 0 for p in index.files())} files with symbols")
    print(f"queries: {len(queries)}\n")

    semantic = SemanticRetriever(files)

    methods = [
        ("grep", lambda q: grep_baseline(files, q, top_k=TOPK)),
        ("semantic", lambda q: semantic.search(q, top_k=TOPK)),
        (
            "hybrid",
            lambda q: HybridRetriever(index, files=files, enable_structural=False).retrieve(q, top_k=TOPK),
        ),
        (
            "hybrid+graph",
            lambda q: HybridRetriever(index, files=files).retrieve(q, top_k=TOPK),
        ),
    ]

    results = [run_method(name, fn, queries, relevant_map) for name, fn in methods]

    header = f"{'method':<14} {'Recall@5':>9} {'Recall@10':>10} {'MRR':>7} {'Hit@5':>7} {'Hit@10':>8}"
    print(header)
    print("-" * len(header))
    for r in results:
        a = r["aggregate"]
        print(f"{r['method']:<14} {a['recall@5']:>9.4f} {a['recall@10']:>10.4f} {a['mrr']:>7.4f} {a['hit@5']:>7.4f} {a['hit@10']:>8.4f}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "corpus": str(CORPUS_ROOT.relative_to(_PYTHON_DIR.parent)),
        "num_files": len(files),
        "num_queries": len(queries),
        "topk": TOPK,
        "results": results,
    }, indent=2, ensure_ascii=False))
    print(f"\nraw results saved to {RESULTS_PATH.relative_to(_PYTHON_DIR.parent)}")


if __name__ == "__main__":
    main()
