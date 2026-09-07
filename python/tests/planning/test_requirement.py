"""RequirementParser tests — kind classification, issue detection, stack
trace and test-failure extraction."""

import sys
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.planning import RequirementKind, RequirementParser  # noqa: E402


class TestKindDetection(unittest.TestCase):
    def setUp(self):
        self.parser = RequirementParser()

    def test_bug(self):
        self.assertEqual(self.parser.detect_kind("fix the broken login bug"), RequirementKind.BUG)

    def test_feature(self):
        self.assertEqual(self.parser.detect_kind("implement support for file upload"),
                         RequirementKind.FEATURE)

    def test_refactor(self):
        self.assertEqual(self.parser.detect_kind("refactor and simplify the handler"),
                         RequirementKind.REFACTOR)

    def test_test(self):
        self.assertEqual(self.parser.detect_kind("add unit tests to raise coverage"),
                         RequirementKind.TEST)

    def test_documentation(self):
        self.assertEqual(self.parser.detect_kind("document the API in the README"),
                         RequirementKind.DOCUMENTATION)

    def test_chinese_keywords(self):
        self.assertEqual(self.parser.detect_kind("修复登录页面的错误"), RequirementKind.BUG)
        self.assertEqual(self.parser.detect_kind("新增导出功能"), RequirementKind.FEATURE)

    def test_defaults_to_feature(self):
        self.assertEqual(self.parser.detect_kind("make the dashboard nicer"),
                         RequirementKind.FEATURE)

    def test_bug_beats_feature_on_tie(self):
        # "fix" and "add" both appear once — bug wins the tie
        self.assertEqual(self.parser.detect_kind("add a fix"), RequirementKind.BUG)


class TestParse(unittest.TestCase):
    def setUp(self):
        self.parser = RequirementParser()

    def test_title_from_first_line(self):
        req = self.parser.parse("Make the CLI faster\n\nDetails here")
        self.assertEqual(req.title, "Make the CLI faster")
        self.assertEqual(req.kind, RequirementKind.FEATURE)

    def test_issue_like_detection(self):
        text = "## Description\nDo the thing\n\n### Steps to Reproduce\n1. run it"
        self.assertTrue(self.parser.parse(text).issue_like)

    def test_plain_text_not_issue(self):
        self.assertFalse(self.parser.parse("just a plain requirement").issue_like)

    def test_parse_issue(self):
        req = self.parser.parse_issue("Fix crash on startup", "## Steps to Reproduce\n1. launch")
        self.assertEqual(req.title, "Fix crash on startup")
        self.assertEqual(req.kind, RequirementKind.BUG)
        self.assertTrue(req.issue_like)

    def test_stack_trace_frames(self):
        text = ('Traceback (most recent call last):\n'
                '  File "/app/parser.py", line 42, in parse_body\n'
                '  File "/app/util.py", line 7, in decode\n'
                'ValueError: bad encoding')
        req = self.parser.parse(text)
        self.assertEqual(req.kind, RequirementKind.BUG)
        self.assertEqual([(f.file, f.line, f.function) for f in req.frames],
                         [("/app/parser.py", 42, "parse_body"),
                          ("/app/util.py", 7, "decode")])
        self.assertEqual(req.related_files, ["/app/parser.py", "/app/util.py"])
        self.assertIn("Traceback", req.stack_trace)

    def test_test_failure_extraction(self):
        text = ('FAILED tests/test_a.py::TestX::test_one - AssertionError: assert 1 == 2\n'
                'FAILED tests/test_b.py::test_two - KeyError: x\n')
        req = self.parser.parse(text)
        self.assertEqual(req.kind, RequirementKind.TEST)
        self.assertEqual(req.test_failure.failed_tests,
                         ["tests/test_a.py::TestX::test_one", "tests/test_b.py::test_two"])
        self.assertEqual(req.test_failure.assertion_message, "assert 1 == 2")

    def test_no_failure_no_test_failure(self):
        req = self.parser.parse("add a feature")
        self.assertIsNone(req.test_failure)
        self.assertEqual(req.frames, [])

    def test_kind_members(self):
        self.assertEqual(
            {k.value for k in RequirementKind},
            {"bug", "feature", "refactor", "test", "documentation"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
