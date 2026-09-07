"""VerificationPipeline tests — real commands against a tmp copy of the
bug_repo fixture (never the fixture itself, so no .pytest_cache/__pycache__
pollution). Detection probes are injected for the deterministic cases."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.verify import (  # noqa: E402
    ToolDetection,
    VerificationPipeline,
    detect_tools,
)

BUG_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "bug_repo"


def _copy_bug_repo(tmp: Path) -> Path:
    dst = tmp / "bug_repo"
    shutil.copytree(BUG_REPO, dst)
    return dst


def _tools(**overrides) -> ToolDetection:
    base = dict(python=sys.executable, test_runner="pytest",
                lint_tool=None, type_tool=None, probe_log={})
    base.update(overrides)
    return ToolDetection(**base)


def _fixed_repo(tmp: Path) -> Path:
    """A copy with ONLY the injected bug fixed (for green-path tests).
    The replace targets the multiply body uniquely — add() keeps its
    correct `return a + b`."""
    repo = _copy_bug_repo(tmp)
    utils = repo / "pkg" / "utils.py"
    utils.write_text(utils.read_text().replace(
        "def multiply(a, b):\n"
        "    # INJECTED BUG: multiplies by adding. The unit test below must fail.\n"
        "    return a + b\n",
        "def multiply(a, b):\n"
        "    return a * b\n"))
    return repo


class TestDetection(unittest.TestCase):
    def test_real_probe_finds_pytest_and_records_every_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            det = detect_tools(_copy_bug_repo(Path(tmp)))
        self.assertEqual(det.test_runner, "pytest")  # the venv really has it
        self.assertIn("pytest", det.probe_log)
        self.assertIn("ruff", det.probe_log)
        self.assertIn("mypy", det.probe_log)
        self.assertEqual(det.probe_log["pytest"], "detected (python -m pytest)")

    def test_injected_probe_selects_ruff_and_unittest(self):
        def probe(argv):
            ok = any(t in argv for t in ("ruff", "unittest", "mypy"))
            return SimpleNamespace(returncode=0 if ok else 1)

        with tempfile.TemporaryDirectory() as tmp:
            det = detect_tools(_copy_bug_repo(Path(tmp)), probe=probe)
        self.assertEqual(det.test_runner, "unittest")  # pytest probe failed
        self.assertEqual(det.lint_tool, "ruff")
        self.assertEqual(det.type_tool, "mypy")

    def test_injected_probe_finds_nothing(self):
        def probe(argv):
            return SimpleNamespace(returncode=1)

        with tempfile.TemporaryDirectory() as tmp:
            det = detect_tools(_copy_bug_repo(Path(tmp)), probe=probe)
        self.assertIsNone(det.test_runner)
        self.assertIsNone(det.lint_tool)
        self.assertIsNone(det.type_tool)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_full_run_fails_at_targeted_test_with_real_pytest(self):
        repo = _copy_bug_repo(self.tmp)
        report = VerificationPipeline(repo, changed_files=["pkg/utils.py"]).run()
        self.assertFalse(report.passed)
        failure = report.first_failure
        self.assertEqual(failure.stage, "targeted_test")
        self.assertTrue(any("test_multiply" in t for t in failure.failed_tests),
                        failure.failed_tests)
        self.assertEqual(failure.exit_code, 1)
        self.assertIn("pytest", failure.command)
        # The chain stopped there: no later stages ran.
        self.assertEqual([s.stage for s in report.stages][-1], "targeted_test")
        # The selection record is honest about absent tools.
        self.assertIn("skipped", report.selected_tools["lint"] or "skipped")
        self.assertIn("skipped", report.selected_tools["typecheck"] or "skipped")
        self.assertEqual(report.selected_tools["test_runner"], "pytest")

    def test_syntax_stage_catches_broken_file_first(self):
        repo = _copy_bug_repo(self.tmp)
        (repo / "broken.py").write_text("def broken(:\n")
        report = VerificationPipeline(repo, changed_files=["broken.py"]).run()
        failure = report.first_failure
        self.assertEqual(failure.stage, "syntax")
        self.assertEqual(failure.exit_code, 1)
        self.assertIn("SyntaxError", failure.stderr)

    def test_green_path_runs_all_stages_in_order(self):
        repo = _fixed_repo(self.tmp)
        report = VerificationPipeline(repo, changed_files=["pkg/utils.py"]).run()
        self.assertTrue(report.passed)
        stages = [s.stage for s in report.stages]
        self.assertEqual(stages[:7], ["syntax", "lint", "typecheck",
                                      "targeted_test", "unit_test",
                                      "integration_test", "regression_test"])
        self.assertEqual(stages[7], "reviewer")
        # Unit excludes the integration directory; integration runs it;
        # regression runs the whole suite.
        self.assertNotIn("integration", report.stage("unit_test").command)
        self.assertIn("integration", report.stage("integration_test").command)
        self.assertNotIn("tests/", report.stage("regression_test").command)

    def test_lint_and_typecheck_skip_cleanly_when_tools_absent(self):
        repo = _fixed_repo(self.tmp)
        report = VerificationPipeline(
            repo, changed_files=["pkg/utils.py"],
            tools=_tools(test_runner="pytest")).run()
        for stage, phrase in (("lint", "no lint tool detected"),
                              ("typecheck", "no type checker detected")):
            result = report.stage(stage)
            self.assertEqual(result.status, "skipped", stage)
            self.assertIn(phrase, result.detail)
        self.assertEqual(report.stage("reviewer").status, "skipped")

    def test_reviewer_hook_runs_last_and_receives_report(self):
        repo = _fixed_repo(self.tmp)
        seen = []

        def reviewer(report):
            seen.append(report.first_failure is None)
            return "looks good"

        report = VerificationPipeline(repo, changed_files=["pkg/utils.py"],
                                      tools=_tools(), reviewer=reviewer).run()
        self.assertEqual(report.stage("reviewer").status, "passed")
        self.assertIn("looks good", report.stage("reviewer").detail)
        self.assertEqual(seen, [True])

    def test_targeted_mapping_from_changed_files(self):
        repo = _fixed_repo(self.tmp)
        pipe = VerificationPipeline(repo, changed_files=["pkg/utils.py"],
                                    tools=_tools())
        self.assertEqual(pipe._map_target_tests(), ["tests/test_utils.py"])
        pipe = VerificationPipeline(repo, changed_files=["tests/test_utils.py"],
                                    tools=_tools())
        self.assertEqual(pipe._map_target_tests(), ["tests/test_utils.py"])
        pipe = VerificationPipeline(repo, changed_files=["pkg/other.py"],
                                    tools=_tools())
        self.assertEqual(pipe._map_target_tests(), [])

    def test_targeted_test_skips_without_mapping_or_targets(self):
        repo = _fixed_repo(self.tmp)
        report = VerificationPipeline(repo, tools=_tools()).run()
        self.assertEqual(report.stage("targeted_test").status, "skipped")
        self.assertIn("no test file maps", report.stage("targeted_test").detail)

    def test_unittest_fallback_runs_real_commands(self):
        repo = _fixed_repo(self.tmp)
        report = VerificationPipeline(
            repo, changed_files=["pkg/utils.py"],
            tools=_tools(test_runner="unittest")).run()
        unit = report.stage("unit_test")
        self.assertIn("python -m unittest", unit.command)
        self.assertIn("test_utils", unit.command)
        # unittest discover for regression (no file args).
        self.assertIn("discover", report.stage("regression_test").command)
        # Targeted maps the changed module to its unittest module path.
        self.assertIn("test_utils", report.stage("targeted_test").command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
