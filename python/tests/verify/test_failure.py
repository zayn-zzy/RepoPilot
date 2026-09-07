"""The unified VerificationFailure / StageResult / VerificationReport model."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.verify import (  # noqa: E402
    StageResult,
    VerificationFailure,
    VerificationReport,
)


class TestVerificationFailure(unittest.TestCase):
    def test_fields_match_spec(self):
        f = VerificationFailure(
            stage="unit_test", command="python -m pytest -q tests/test_utils.py",
            exit_code=1, stdout="...", stderr="E assert 7 == 12",
            failed_tests=["tests/test_utils.py::test_multiply"],
            related_files=["tests/test_utils.py", "pkg/utils.py"],
        )
        self.assertEqual(f.stage, "unit_test")
        self.assertEqual(f.exit_code, 1)
        self.assertEqual(f.failed_tests, ["tests/test_utils.py::test_multiply"])
        self.assertEqual(f.related_files, ["tests/test_utils.py", "pkg/utils.py"])

    def test_summary_is_the_failure_summarizer_output(self):
        f = VerificationFailure(
            stage="targeted_test", command="python -m pytest -q tests/test_utils.py",
            exit_code=1, stdout="F.", stderr="E assert 7 == 12",
            failed_tests=["tests/test_utils.py::test_multiply"],
            related_files=["pkg/utils.py"],
        )
        s = f.summary()
        for needle in ("Stage: targeted_test", "Command: python -m pytest",
                       "Exit code: 1", "Failed tests: tests/test_utils.py::test_multiply",
                       "Related files: pkg/utils.py", "E assert 7 == 12"):
            self.assertIn(needle, s)

    def test_summary_tails_long_output(self):
        f = VerificationFailure(stage="syntax", command="py_compile", exit_code=1,
                                stderr="x" * 5000)
        s = f.summary()
        self.assertLessEqual(len(s), 7000)
        self.assertIn("Stderr:", s)


class TestStageResult(unittest.TestCase):
    def test_failure_only_for_failed_stage(self):
        passed = StageResult(stage="lint", status="passed", exit_code=0)
        skipped = StageResult(stage="typecheck", status="skipped",
                              detail="no mypy")
        failed = StageResult(stage="syntax", status="failed", command="py_compile",
                             exit_code=1, stderr="SyntaxError: invalid syntax")
        self.assertIsNone(passed.failure)
        self.assertIsNone(skipped.failure)
        f = failed.failure
        self.assertIsNotNone(f)
        self.assertEqual((f.stage, f.exit_code), ("syntax", 1))
        self.assertIn("SyntaxError", f.stderr)


class TestVerificationReport(unittest.TestCase):
    def _report(self, statuses):
        return VerificationReport(
            root=Path("/tmp/x"),
            stages=[StageResult(stage=s, status=st) for s, st in statuses],
        )

    def test_passed_and_first_failure(self):
        self.assertTrue(self._report([("syntax", "passed"),
                                      ("lint", "skipped")]).passed)
        r = self._report([("syntax", "passed"), ("unit_test", "failed")])
        self.assertFalse(r.passed)
        self.assertEqual(r.first_failure.stage, "unit_test")

    def test_stage_lookup(self):
        r = self._report([("lint", "skipped")])
        self.assertEqual(r.stage("lint").status, "skipped")
        self.assertIsNone(r.stage("unit_test"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
