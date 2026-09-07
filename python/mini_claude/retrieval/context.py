"""Token-aware Context Builder — packs ranked retrieval hits into an LLM
context that stays within a token budget. Full file contents go first; when
the budget runs low, files degrade to symbol summaries (signatures + line
numbers from the repository index) instead of being dropped outright."""

from __future__ import annotations

from .model import RetrievalHit


def estimate_tokens(text: str) -> int:
    """Cheap deterministic estimate: UTF-8 bytes / 4 ≈ tokens."""
    return (len(text.encode("utf-8")) + 3) // 4


class ContextBuilder:
    def __init__(self, files: dict[str, str], index=None, max_file_tokens: int = 4000):
        self.files = files
        self.index = index
        self.max_file_tokens = max_file_tokens

    def build_context(self, hits: list[RetrievalHit], token_budget: int) -> str:
        """Assemble context for the ranked hits until the token budget is
        exhausted. Returns the assembled text (empty string when the budget
        cannot even hold a header)."""
        sections: list[str] = []
        used = 0
        overflow_files: list[str] = []

        for hit in hits:
            path = hit.file_path
            header = f"### {path}"
            if hit.symbols:
                header += f"  (symbols: {', '.join(hit.symbols)})"
            header_tokens = estimate_tokens(header) + 1
            if used + header_tokens >= token_budget:
                overflow_files.append(path)
                continue

            content = self.files.get(path, "")
            full_tokens = estimate_tokens(content) + header_tokens
            if used + full_tokens <= token_budget and full_tokens <= self.max_file_tokens:
                sections.append(f"{header}\n{content}")
                used += full_tokens + 1
                continue

            # Budget can't hold the full file: fall back to a symbol summary.
            summary = self._symbol_summary(path)
            if summary:
                summary_tokens = estimate_tokens(summary) + header_tokens
                if used + summary_tokens <= token_budget:
                    sections.append(f"{header}\n{summary}")
                    used += summary_tokens + 1
                    continue

            # Even the summary doesn't fit — note it and move on.
            overflow_files.append(path)

        if overflow_files:
            note = f"\n(... {len(overflow_files)} more candidates omitted: budget)"
            note_tokens = estimate_tokens(note)
            if used + note_tokens <= token_budget:
                sections.append(note)
        return "\n\n".join(sections)

    def _symbol_summary(self, path: str) -> str:
        if self.index is None:
            return ""
        lines = []
        for sym in self.index.symbols_in_file(path):
            lines.append(f"{sym.signature or sym.qualified_name}  # L{sym.location.start_line}")
        return "\n".join(lines)
