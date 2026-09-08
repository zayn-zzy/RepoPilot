"""Repository Task suite — 24 tasks, 6 categories × 4.

Each template is a small CORRECT project with a full test suite. A task
is the template with defects injected into 1-2 files (the canonical fix
restores the template contents). The template's own test suite is the
grading test: it FAILS on the task repo and PASSES after the fix —
verified by the suite-integrity test for every task.

Ground truth for retrieval metrics: the files the canonical fix touches.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ─── templates: correct projects with complete test suites ─────────

TEMPLATES: dict[str, dict[str, str]] = {
    "calc": {
        "calc.py": '''"""Small arithmetic utilities."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b


def product(values):
    result = 1
    for v in values:
        result *= v
    return result
''',
        "stats.py": '''"""Statistics helpers."""


def mean(values):
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def total(values):
    return sum(values)
''',
        "tests/test_calc.py": '''from calc import add, subtract, multiply, divide, product
from stats import mean, median, total


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(3, 4) == 12


def test_divide():
    assert divide(10, 4) == 2.5


def test_divide_by_zero():
    import pytest
    with pytest.raises(ValueError):
        divide(1, 0)


def test_product():
    assert product([2, 3, 4]) == 24


def test_mean():
    assert mean([2, 4, 6]) == 4.0


def test_mean_empty():
    import pytest
    with pytest.raises(ValueError):
        mean([])


def test_median():
    assert median([1, 2, 3]) == 2
    assert median([1, 2, 3, 4]) == 2.5


def test_total():
    assert total([1, 2, 3]) == 6
''',
    },
    "shop": {
        "pricing.py": '''"""Pricing rules for the shop."""


def apply_discount(price, discount):
    """discount is a fraction in [0, 1)."""
    if discount < 0 or discount >= 1:
        raise ValueError("discount must be in [0, 1)")
    return price * (1 - discount)


def final_price(price, discount, tax_rate):
    discounted = apply_discount(price, discount)
    return discounted * (1 + tax_rate)
''',
        "order.py": '''"""Order totals built on pricing rules."""
from pricing import final_price


def order_total(items, discount=0.0, tax_rate=0.0, coupon=0.0):
    total = 0
    for item in items:
        total += final_price(item["price"], discount, tax_rate)
    return total - coupon
''',
        "report.py": '''"""Summary reports over orders."""
from pricing import final_price


def summarize(items, discount=0.0, tax_rate=0.0):
    prices = [final_price(i["price"], discount, tax_rate) for i in items]
    return {
        "count": len(items),
        "total": sum(prices),
        "average": sum(prices) / len(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
    }
''',
        "tests/test_shop.py": '''from pricing import apply_discount, final_price
from order import order_total
from report import summarize


def test_discount():
    assert apply_discount(100, 0.2) == 80.0


def test_discount_bounds():
    import pytest
    with pytest.raises(ValueError):
        apply_discount(100, 1.5)


def test_final_price():
    assert final_price(100, 0.2, 0.1) == 88.0


def test_order_total():
    items = [{"price": 100}, {"price": 50}]
    assert order_total(items, discount=0.2, tax_rate=0.1) == 132.0


def test_order_coupon():
    items = [{"price": 100}]
    assert order_total(items, discount=0.2, tax_rate=0.1, coupon=10.0) == 78.0


def test_summarize():
    items = [{"price": 100}, {"price": 50}]
    s = summarize(items, discount=0.2, tax_rate=0.1)
    assert s["count"] == 2
    assert s["total"] == 132.0
    assert s["average"] == 66.0
    assert s["max_price"] == 88.0


def test_summarize_empty():
    assert summarize([]) == {"count": 0, "total": 0.0,
                             "average": 0.0, "max_price": 0.0}
''',
    },
    "text": {
        "normalize.py": '''"""Text normalization."""


def strip_whitespace(text):
    return text.strip()


def lowercase(text):
    return text.lower()


def normalize(text):
    return lowercase(strip_whitespace(text))
''',
        "tokenizer.py": '''"""Word tokenization."""
from normalize import normalize


def word_count(text):
    if not text or not text.strip():
        return 0
    return len(normalize(text).split())


def tokenize(text):
    if not text or not text.strip():
        return []
    return normalize(text).split()
''',
        "tests/test_text.py": '''from normalize import normalize, lowercase, strip_whitespace
from tokenizer import tokenize, word_count


def test_normalize():
    assert normalize("  Hello WORLD  ") == "hello world"


def test_word_count():
    assert word_count("one two three") == 3
    assert word_count("") == 0
    assert word_count("   ") == 0


def test_word_count_multiline():
    assert word_count("one two\\nthree") == 3


def test_tokenize():
    assert tokenize("  A   B C ") == ["a", "b", "c"]
    assert tokenize("") == []


def test_lowercase():
    assert lowercase("ABC") == "abc"


def test_strip():
    assert strip_whitespace("  x  ") == "x"
''',
    },
}


@dataclass(frozen=True)
class TaskSpec:
    """One repository task: template + injected defects (buggy contents).
    The canonical fix restores the template contents of those files."""

    task_id: str
    category: str
    template: str
    description: str
    defects: dict[str, str] = field(default_factory=dict)  # relpath → buggy content

    @property
    def relevant_files(self) -> list[str]:
        """Ground truth: the files the canonical fix touches."""
        return sorted(self.defects)


# ─── the 24 task specs (6 categories × 4) ─────────────────────────

TASK_SPECS: list[TaskSpec] = [
    # Bug Fix ×4
    TaskSpec("calc-bug-multiply", "bug_fix", "calc",
             "The multiply() function returns wrong results: multiplying "
             "two numbers gives their sum. Fix the multiplication bug.",
             {"calc.py": TEMPLATES["calc"]["calc.py"].replace(
                 "    return a * b\n", "    return a + b\n")}),
    TaskSpec("calc-bug-subtract", "bug_fix", "calc",
             "The subtract() function computes a + b instead of a - b. "
             "Fix it so subtraction is correct.",
             {"calc.py": TEMPLATES["calc"]["calc.py"].replace(
                 "    return a - b\n", "    return a + b\n")}),
    TaskSpec("shop-bug-discount", "bug_fix", "shop",
             "apply_discount() applies the discount WRONG: a 20% discount "
             "on 100 returns 20 instead of 80. Fix the discount math.",
             {"pricing.py": TEMPLATES["shop"]["pricing.py"].replace(
                 "    return price * (1 - discount)\n",
                 "    return price * discount\n")}),
    TaskSpec("text-bug-tokenize", "bug_fix", "text",
             "tokenize() returns the original-cased words: tokens come out "
             "in uppercase even though the suite expects normalized "
             "lowercase tokens. Fix tokenize.",
             {"tokenizer.py": TEMPLATES["text"]["tokenizer.py"].replace(
                 "    return normalize(text).split()\n",
                 "    return text.split()\n")}),

    # API Bug ×4 (a function's contract is violated: wrong return/args)
    TaskSpec("calc-api-divide", "api_bug", "calc",
             "divide() truncates its result to an integer, breaking the "
             "API contract that it returns the exact quotient "
             "(10/4 must be 2.5). Fix the contract violation.",
             {"calc.py": TEMPLATES["calc"]["calc.py"].replace(
                 "    return a / b\n", "    return a // b\n")}),
    TaskSpec("calc-api-total", "api_bug", "calc",
             "total() returns a string like 'total: 6' instead of a "
             "number. Callers expect a numeric total. Restore the numeric "
             "return type.",
             {"stats.py": TEMPLATES["calc"]["stats.py"].replace(
                 "    return sum(values)\n",
                 "    return f\"total: {sum(values)}\"\n")}),
    TaskSpec("shop-api-final-price", "api_bug", "shop",
             "final_price() ignores the tax_rate argument entirely: it "
             "returns the discounted price without adding tax. The "
             "argument must take effect.",
             {"pricing.py": TEMPLATES["shop"]["pricing.py"].replace(
                 "    discounted = apply_discount(price, discount)\n"
                 "    return discounted * (1 + tax_rate)\n",
                 "    return apply_discount(price, discount)\n")}),
    TaskSpec("text-api-word-count", "api_bug", "text",
             "word_count() returns the list of words instead of the count "
             "of words, violating its contract. Return an integer count.",
             {"tokenizer.py": TEMPLATES["text"]["tokenizer.py"].replace(
                 "    return len(normalize(text).split())\n",
                 "    return normalize(text).split()\n")}),

    # Boundary Condition ×4
    TaskSpec("calc-boundary-mean-empty", "boundary", "calc",
             "mean([]) crashes with a ZeroDivisionError instead of "
             "raising the documented ValueError. Guard the empty "
             "sequence at the boundary.",
             {"stats.py": TEMPLATES["calc"]["stats.py"].replace(
                 "    if not values:\n"
                 "        raise ValueError(\"mean of empty sequence\")\n"
                 "    return sum(values) / len(values)\n",
                 "    return sum(values) / len(values)\n")}),
    TaskSpec("calc-boundary-median-even", "boundary", "calc",
             "median() of an even-length list returns the lower middle "
             "element instead of the average of the two middles "
             "([1,2,3,4] must give 2.5). Handle the even-length boundary.",
             {"stats.py": TEMPLATES["calc"]["stats.py"].replace(
                 "    if n % 2 == 1:\n"
                 "        return ordered[mid]\n"
                 "    return (ordered[mid - 1] + ordered[mid]) / 2\n",
                 "    return ordered[mid]\n")}),
    TaskSpec("shop-boundary-discount", "boundary", "shop",
             "apply_discount() accepts discount values of 1.0 or more "
             "(and negatives), producing nonsense prices. Enforce the "
             "[0, 1) boundary by raising ValueError.",
             {"pricing.py": TEMPLATES["shop"]["pricing.py"].replace(
                 "    if discount < 0 or discount >= 1:\n"
                 "        raise ValueError(\"discount must be in [0, 1)\")\n"
                 "    return price * (1 - discount)\n",
                 "    return price * (1 - discount)\n")}),
    TaskSpec("text-boundary-empty-count", "boundary", "text",
             "word_count('') returns 1 instead of 0 — the empty-string "
             "guard returns a hardcoded 1. Fix the empty-input boundary.",
             {"tokenizer.py": TEMPLATES["text"]["tokenizer.py"].replace(
                 "    if not text or not text.strip():\n"
                 "        return 0\n"
                 "    return len(normalize(text).split())\n",
                 "    if not text:\n"
                 "        return 1\n"
                 "    return len(normalize(text).split())\n")}),

    # Feature ×4 (a capability was removed; re-implement it)
    TaskSpec("calc-feature-product", "feature", "calc",
             "Add a product(values) function that multiplies every "
             "element of the list together (product([2,3,4]) == 24). "
             "The test suite already expects it.",
             {"calc.py": TEMPLATES["calc"]["calc.py"].replace(
                 "def product(values):\n"
                 "    result = 1\n"
                 "    for v in values:\n"
                 "        result *= v\n"
                 "    return result\n",
                 "")}),
    TaskSpec("shop-feature-coupon", "feature", "shop",
             "order_total() must support a coupon argument: a fixed "
             "amount subtracted AFTER discounts and tax are applied. "
             "The test suite already passes coupon=10.0.",
             {"order.py": TEMPLATES["shop"]["order.py"].replace(
                 "def order_total(items, discount=0.0, tax_rate=0.0, coupon=0.0):\n"
                 "    total = 0\n"
                 "    for item in items:\n"
                 "        total += final_price(item[\"price\"], discount, tax_rate)\n"
                 "    return total - coupon\n",
                 "def order_total(items, discount=0.0, tax_rate=0.0):\n"
                 "    total = 0\n"
                 "    for item in items:\n"
                 "        total += final_price(item[\"price\"], discount, tax_rate)\n"
                 "    return total\n")}),
    TaskSpec("text-feature-multiline-count", "feature", "text",
             "word_count() only counts the first line of multi-line "
             "text. Make it count words across all lines.",
             {"tokenizer.py": TEMPLATES["text"]["tokenizer.py"].replace(
                 "    return len(normalize(text).split())\n",
                 "    return len(normalize(text).splitlines()[0].split())\n")}),
    TaskSpec("shop-feature-max-price", "feature", "shop",
             "summarize() is missing the max_price field in its result "
             "dictionary. The test suite expects it (0.0 for empty "
             "input).",
             {"report.py": TEMPLATES["shop"]["report.py"].replace(
                 "    return {\n"
                 "        \"count\": len(items),\n"
                 "        \"total\": sum(prices),\n"
                 "        \"average\": sum(prices) / len(prices) if prices else 0.0,\n"
                 "        \"max_price\": max(prices) if prices else 0.0,\n"
                 "    }\n",
                 "    return {\n"
                 "        \"count\": len(items),\n"
                 "        \"total\": sum(prices),\n"
                 "        \"average\": sum(prices) / len(prices) if prices else 0.0,\n"
                 "    }\n")}),

    # Cross-file Change ×4 (the canonical fix touches two files)
    TaskSpec("calc-cross-add-total", "cross_file", "calc",
             "Two related numeric defects: add() computes a - b, and "
             "total() halves every sum. Fix both files.",
             {
                 "calc.py": TEMPLATES["calc"]["calc.py"].replace(
                     "    return a + b\n", "    return a - b\n"),
                 "stats.py": TEMPLATES["calc"]["stats.py"].replace(
                     "    return sum(values)\n", "    return sum(values) / 2\n"),
             }),
    TaskSpec("shop-cross-report-pricing", "cross_file", "shop",
             "Report totals disagree with listed prices: report.py "
             "hardcodes a 10% tax, and pricing.py's apply_discount uses "
             "the inverse formula. Fix both files so reports match "
             "order totals.",
             {
                 "report.py": TEMPLATES["shop"]["report.py"].replace(
                     "    prices = [final_price(i[\"price\"], discount, tax_rate) for i in items]\n",
                     "    prices = [i[\"price\"] * 0.9 for i in items]\n"),
                 "pricing.py": TEMPLATES["shop"]["pricing.py"].replace(
                     "    return price * (1 - discount)\n",
                     "    return price * (1 + discount)\n"),
             }),
    TaskSpec("text-cross-normalize-tokenize", "cross_file", "text",
             "Two related text defects: lowercase() uppercases instead, "
             "and tokenize() returns a phantom token for blank input. "
             "Fix both files.",
             {
                 "normalize.py": TEMPLATES["text"]["normalize.py"].replace(
                     "    return text.lower()\n", "    return text.upper()\n"),
                 "tokenizer.py": TEMPLATES["text"]["tokenizer.py"].replace(
                     "    if not text or not text.strip():\n"
                     "        return []\n"
                     "    return normalize(text).split()\n",
                     "    return normalize(text).split()\n"),
             }),
    TaskSpec("shop-cross-order-report", "cross_file", "shop",
             "With both a discount and a tax set, order totals are "
             "wrong: order.py passes the two rates swapped, and "
             "report.py computes the average over len-1 items. Fix "
             "both files.",
             {
                 "order.py": TEMPLATES["shop"]["order.py"].replace(
                     "        total += final_price(item[\"price\"], discount, tax_rate)\n",
                     "        total += final_price(item[\"price\"], tax_rate, discount)\n"),
                 "report.py": TEMPLATES["shop"]["report.py"].replace(
                     "        \"average\": sum(prices) / len(prices) if prices else 0.0,\n",
                     "        \"average\": sum(prices) / (len(prices) - 1) if len(prices) > 1 else 0.0,\n"),
             }),

    # Repository Understanding ×4 (indirect descriptions — the agent must
    # locate the right code first)
    TaskSpec("calc-understanding-total", "understanding", "calc",
             "A downstream consumer treats the output of one of the "
             "numeric aggregation helpers as a number, but that helper "
             "now returns a labelled string. Find the helper and make "
             "it return a plain number again.",
             {"stats.py": TEMPLATES["calc"]["stats.py"].replace(
                 "    return sum(values)\n",
                 "    return f\"total: {sum(values)}\"\n")}),
    TaskSpec("shop-understanding-rates", "understanding", "shop",
             "When a purchase has both a promotional rate and a "
             "government rate applied, the final charge comes out "
             "wrong. One of the two rates is being applied in the "
             "other's place. Locate the miscalculation and fix it.",
             {"order.py": TEMPLATES["shop"]["order.py"].replace(
                 "        total += final_price(item[\"price\"], discount, tax_rate)\n",
                 "        total += final_price(item[\"price\"], tax_rate, discount)\n")}),
    TaskSpec("text-understanding-whitespace", "understanding", "text",
             "Leading and trailing whitespace sometimes survives into "
             "the normalized output even though casing is handled "
             "correctly. Find where normalization skips whitespace "
             "stripping and fix it.",
             {"normalize.py": TEMPLATES["text"]["normalize.py"].replace(
                 "    return lowercase(strip_whitespace(text))\n",
                 "    return lowercase(text)\n")}),
    TaskSpec("calc-understanding-mean", "understanding", "calc",
             "Reported averages are consistently about half of what "
             "they should be. Something in the statistics helper "
             "halves every result. Track it down and correct it.",
             {"stats.py": TEMPLATES["calc"]["stats.py"].replace(
                 "    return sum(values) / len(values)\n",
                 "    return sum(values) / len(values) / 2\n")}),
]

CATEGORIES = ("bug_fix", "feature", "cross_file", "api_bug",
              "boundary", "understanding")


@dataclass
class RepositoryTask:
    """A built task repo on disk."""

    task_id: str
    category: str
    template: str
    description: str
    root: Path
    relevant_files: list[str]


def build_task_repos(out_dir: str | Path, specs: list[TaskSpec] | None = None
                     ) -> list[RepositoryTask]:
    """Materialize the task repos: template files with the defects
    injected + the template's test suite (the grading tests)."""
    out_dir = Path(out_dir)
    specs = specs if specs is not None else TASK_SPECS
    tasks = []
    for spec in specs:
        repo = out_dir / spec.task_id
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir(parents=True)
        for relpath, content in TEMPLATES[spec.template].items():
            target = repo / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(spec.defects.get(relpath, content))
        (repo / "task.json").write_text(json.dumps({
            "task_id": spec.task_id,
            "category": spec.category,
            "template": spec.template,
            "description": spec.description,
            "relevant_files": spec.relevant_files,
        }, indent=2))
        tasks.append(RepositoryTask(
            task_id=spec.task_id, category=spec.category,
            template=spec.template, description=spec.description,
            root=repo, relevant_files=spec.relevant_files,
        ))
    return tasks


def apply_solution(task: RepositoryTask, template: str | None = None) -> None:
    """Restore the template contents of the defect files — the canonical
    fix — used by the suite-integrity check.

    Also purges __pycache__/.pytest_cache: PEP 552 stores the source
    mtime in pyc headers at SECOND granularity, so a same-length edit
    within the same second can leave stale bytecode behind (observed in
    real runs — the fixed file was on disk but pytest kept executing
    the buggy code)."""
    template = template or task.template
    for relpath, content in TEMPLATES[template].items():
        (task.root / relpath).write_text(content)
    for cache in ("__pycache__", ".pytest_cache"):
        for p in task.root.rglob(cache):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
