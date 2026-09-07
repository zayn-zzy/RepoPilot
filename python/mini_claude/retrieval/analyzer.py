"""QueryAnalyzer — turns a free-form requirement/query into structured
retrieval intents: terms (lexical), identifiers/symbol hints (structural),
path hints (file/module targets), and a kind hint (class/function/method)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+[.](?:py|md|json|toml)|[A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)+")
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_KIND_WORDS = {
    "class": "class", "classes": "class",
    "function": "function", "functions": "function", "func": "function",
    "method": "method", "methods": "method",
}
# English stopwords dropped from TERMS (retrieval signal), kept out of the
# identifiers/symbol hints — 'to'/'how'/'and' match every file and drown
# meaningful tokens.
STOPWORDS = frozenset("""
a an and are as at be but by can could did do does for from had has have he
her his how i if in into is it its me my no nor not of on or our out so that
the their them then there these they this those to too up us was we were what
when where which while who whom why will with would you your
""".split())


@dataclass
class AnalyzedQuery:
    raw: str
    terms: list[str] = field(default_factory=list)         # lowercased tokens
    identifiers: list[str] = field(default_factory=list)   # original-case tokens
    symbol_hints: list[str] = field(default_factory=list)  # likely symbol names
    path_hints: list[str] = field(default_factory=list)    # file/module path-ish tokens
    kind_hint: str | None = None                           # class | function | method


class QueryAnalyzer:
    def analyze(self, raw: str) -> AnalyzedQuery:
        q = AnalyzedQuery(raw=raw)

        # Quoted strings are literal targets — keep them verbatim as hints.
        for quoted in re.findall(r'["\']([^"\']+)["\']', raw):
            q.symbol_hints.append(quoted)

        for tok in _TOKEN_RE.findall(raw):
            low = tok.lower()
            if low in _KIND_WORDS:
                q.kind_hint = _KIND_WORDS[low]
                continue
            q.identifiers.append(tok)
            if low not in STOPWORDS:
                q.terms.append(low)
            if not tok.islower() or _SNAKE_RE.match(tok):
                q.symbol_hints.append(tok)
            # module paths like mini_claude.agent also hint a symbol scope
            if "." in tok and len(tok.split(".")) > 1:
                q.path_hints.append(tok)

        for m in _PATH_RE.findall(raw):
            if m not in q.path_hints:
                q.path_hints.append(m)

        # de-duplicate, keep order
        q.terms = list(dict.fromkeys(q.terms))
        q.identifiers = list(dict.fromkeys(q.identifiers))
        q.symbol_hints = list(dict.fromkeys(q.symbol_hints))
        q.path_hints = list(dict.fromkeys(q.path_hints))
        return q
