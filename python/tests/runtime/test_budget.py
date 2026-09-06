"""Budget tests — token accounting, cost formula, and limit checks."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.runtime import Budget  # noqa: E402


class TestBudget(unittest.TestCase):
    def test_cost_formula(self):
        # $3/M input, 0.3/M cache read, 3.75/M cache write, 15/M output
        b = Budget()
        b.record_tokens(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_creation=1_000_000)
        self.assertAlmostEqual(b.cost_usd, 3 + 15 + 0.3 + 3.75)

    def test_record_tokens_accumulates(self):
        b = Budget()
        b.record_tokens(input=100, output=50)
        b.record_tokens(input=50, output=25)
        self.assertEqual(b.input_tokens, 150)
        self.assertEqual(b.output_tokens, 75)

    def test_negative_tokens_rejected(self):
        b = Budget()
        with self.assertRaises(ValueError):
            b.record_tokens(input=-1)

    def test_under_limits_not_exceeded(self):
        b = Budget(max_cost_usd=10.0, max_turns=5)
        b.record_tokens(input=1000, output=100)
        status = b.check(turns=3)
        self.assertFalse(status.exceeded)

    def test_cost_limit_exceeded(self):
        b = Budget(max_cost_usd=0.001)
        b.record_tokens(input=1000, output=100)  # $0.0045 > $0.001
        status = b.check(turns=0)
        self.assertTrue(status.exceeded)
        self.assertIn("Cost limit", status.reason)

    def test_turn_limit_exceeded(self):
        b = Budget(max_turns=2)
        self.assertFalse(b.check(turns=1).exceeded)
        status = b.check(turns=2)
        self.assertTrue(status.exceeded)
        self.assertIn("Turn limit", status.reason)

    def test_no_limits_never_exceeded(self):
        b = Budget()
        b.record_tokens(input=10_000_000, output=10_000_000)
        self.assertFalse(b.check(turns=10_000).exceeded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
