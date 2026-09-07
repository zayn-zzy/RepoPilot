"""Structural retrieval — symbol-name matching over the Phase 2
RepositoryIndex, expanded along the dependency graph (1-hop / 2-hop).

Seed files are the ones whose symbols match the query's symbol hints;
expansion follows both edge directions (a file's dependencies AND its
dependents), because "who calls X" matters as much as "what X uses"."""

from __future__ import annotations

from .model import RetrievalHit

EXACT = 3.0
SUBSTRING = 1.5
PREFIX = 1.0
HOP_DECAY = {0: 1.0, 1: 0.5, 2: 0.25}


class StructuralRetriever:
    def __init__(self, index):
        self.index = index
        self._symbols_by_file: dict[str, list] = {}
        for path in index.files():
            syms = index.symbols_in_file(path)
            if syms:
                self._symbols_by_file[path] = syms

    # ─── Symbol matching ─────────────────────────────────────

    def _match_symbol(self, sym, hint: str) -> float:
        name = sym.name
        if name == hint:
            return EXACT
        if hint in name:
            return SUBSTRING
        if name.startswith(hint) or hint.startswith(name):
            return PREFIX
        return 0.0

    def _match_file(self, path: str, hints: list[str]) -> tuple[float, list[str]]:
        best = 0.0
        matched: list[str] = []
        for sym in self._symbols_by_file.get(path, []):
            score = 0.0
            for hint in hints:
                score = max(score, self._match_symbol(sym, hint))
            if score > 0:
                best = max(best, score)
                matched.append(sym.qualified_name)
        return best, matched

    # ─── Expansion ───────────────────────────────────────────

    def expand(self, file_paths: list[str], hops: int = 1) -> dict[str, int]:
        """BFS expansion from the seed files: returns {file -> hop distance}.
        Follows both dependencies and dependents at every step."""
        frontier = set(file_paths)
        distance = {p: 0 for p in file_paths}
        seen = set(frontier)
        for hop in range(1, hops + 1):
            nxt = set()
            for p in frontier:
                for neighbor in (*self.index.file_dependencies(p), *self.index.dependents(p)):
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    distance[neighbor] = hop
                    nxt.add(neighbor)
            frontier = nxt
        return distance

    # ─── Search ──────────────────────────────────────────────

    def search(self, query, top_k: int = 10, max_hop: int = 2) -> list[RetrievalHit]:
        """`query` is an AnalyzedQuery (symbol_hints drive the match) or a
        plain string (tokenized into hints)."""
        if hasattr(query, "symbol_hints"):
            hints = list(query.symbol_hints)
        else:
            import re
            hints = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
        if not hints:
            return []

        seeds: dict[str, float] = {}
        seed_symbols: dict[str, list[str]] = {}
        for path in self._symbols_by_file:
            score, matched = self._match_file(path, hints)
            if score > 0:
                seeds[path] = score
                seed_symbols[path] = matched
        if not seeds:
            return []

        distance = self.expand(list(seeds), hops=max_hop)
        # Propagate scores outward hop by hop: a file's score is the best
        # adjacent score at hop-1, decayed. (Adjacent 2-hop files are 1-hop
        # files, not seeds — looking only at seed neighbors would drop the
        # whole expansion frontier.)
        scores = dict(seeds)
        for hop in range(1, max_hop + 1):
            for path, d in distance.items():
                if d != hop:
                    continue
                best = 0.0
                for n in (*self.index.file_dependencies(path),
                          *self.index.dependents(path)):
                    best = max(best, scores.get(n, 0.0))
                scores[path] = best * HOP_DECAY.get(hop, 0.15)

        hits: list[RetrievalHit] = []
        for path, hop in distance.items():
            score = scores.get(path, 0.0)
            if score > 0:
                hits.append(RetrievalHit(
                    path, score, sources={"structural": score},
                    symbols=seed_symbols.get(path, []),
                ))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
