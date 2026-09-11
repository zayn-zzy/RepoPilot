"""Chinese tokenization for retrieval — queries AND corpus.

jieba word segmentation when installed (proper dictionary-based word
tokens); otherwise CJK character bigrams — a standard, dependency-free
fallback that works well for retrieval. Which method produced the tokens
is always reported (the analyzer records it), so results never pretend.

Only CJK runs are touched; everything else belongs to the ASCII
tokenizers."""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[一-鿿]+")

# Grammar/function characters in queries — dropped like the English
# stopwords so they don't drown meaningful terms.
ZH_STOPWORDS = frozenset("""
的 了 在 是 和 与 及 或 对 把 被 到 从 向 让 使 用 请 帮 我 我们 你 你们
有 无 中 上 下 内 外 前 后 里 这 那 哪 什么 怎么 怎样 如何 为了 给 会
能 要 就 都 也 还 不 没 一个 种 些 点 之 其 它 他 她 呢 吗 吧 啊 哦
""".split())

try:
    import jieba  # noqa: F401
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False


def zh_method() -> str:
    """Which Chinese tokenizer is active: "jieba" or "bigram"."""
    return "jieba" if _HAS_JIEBA else "bigram"


def tokenize_chinese(text: str, *, method: str | None = None) -> list[str]:
    """Chinese retrieval tokens from every CJK run in ``text``: jieba
    words (stopwords dropped) or character bigrams."""
    use = method or zh_method()
    tokens: list[str] = []
    for span in _CJK_RE.findall(text):
        if use == "jieba":
            import jieba
            tokens += [w for w in jieba.cut(span)
                       if w.strip() and w not in ZH_STOPWORDS]
        else:
            tokens += [span[i:i + 2] for i in range(len(span) - 1)]
    return tokens


def cjk_spans(text: str) -> list[str]:
    """The contiguous CJK runs in a text (for literal grep terms)."""
    return _CJK_RE.findall(text)


def expand_cjk_bigrams(text: str) -> str:
    """Rewrite CJK runs as space-separated character bigrams — the
    preprocessing that lets ASCII-only indexers (TF-IDF's token_pattern)
    see Chinese. Single-character runs stay as-is."""
    return _CJK_RE.sub(
        lambda m: (" ".join(m.group(0)[i:i + 2]
                            for i in range(len(m.group(0)) - 1))
                   or m.group(0)),
        text)
