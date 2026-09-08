"""VerificationPipeline sandbox wiring — stage commands route through
the SandboxedCommandRunner (docker argv via a pass-through executor),
and every StageResult records where the command really ran."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.sandbox import SandboxedCommandRunner  # noqa: E402
from mini_claude.verify import VerificationPipeline  # noqa: E402


def _docker_passthrough():
    """Execute the docker argv's command locally (so the pipeline really
    works in 'docker' mode) while recording every argv. The --volume /
    -w flags are translated back to a host cwd — what the docker daemon
    would do."""
    def run(argv, **kwargs):
        run.calls.append(argv)
        cwd = None
        for i, a in enumerate(argv):
            if a == "--volume" and i + 1 < len(argv) and ":/workspace" in argv[i + 1]:
                cwd = Path(argv[i + 1].split(":")[0])
            elif a == "-w" and i + 1 < len(argv):
                rel = argv[i + 1][len("/workspace"):].lstrip("/")
                if cwd is not None and rel:
                    cwd = cwd / rel
        return subprocess.run(argv[-1], shell=True, capture_output=True,
                              text=True, timeout=60,
                              cwd=str(cwd) if cwd is not None else None)
    run.calls = []
    return run


class TestPipelineSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        (self.root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_calc.py").write_text(
            "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")

    def test_stages_run_in_docker_and_record_it(self):
        executor = _docker_passthrough()
        runner = SandboxedCommandRunner(self.root, mode="auto",
                                        availability=lambda: True,
                                        executor=executor)
        report = VerificationPipeline(self.root, sandbox=runner).run()

        self.assertTrue(report.passed)
        ran = [s for s in report.stages
               if s.status in ("passed", "failed") and s.command]
        self.assertTrue(ran)
        self.assertTrue(all(s.sandbox == "docker" for s in ran))
        # The commands really went through the docker argv (the pytest
        # invocation is the shell command at the end of some argv).
        self.assertTrue(any("-m" in a[-1] and "pytest" in a[-1]
                            for a in executor.calls))
        # The selection record names the sandbox mode honestly.
        self.assertIn("sandbox", report.selected_tools)
        self.assertIn("docker", report.selected_tools["sandbox"])

    def test_host_fallback_recorded_per_stage(self):
        runner = SandboxedCommandRunner(self.root, mode="auto",
                                        availability=lambda: False)
        report = VerificationPipeline(self.root, sandbox=runner).run()

        self.assertTrue(report.passed)
        ran = [s for s in report.stages
               if s.status in ("passed", "failed") and s.command]
        self.assertTrue(ran)
        self.assertTrue(all(s.sandbox.startswith("host (") for s in ran))
        self.assertIn("host fallback", report.selected_tools["sandbox"])

    def test_blocked_command_fails_the_stage_honestly(self):
        # 'on' without docker: every command is blocked — the stage fails
        # with the honest sandbox record instead of a silent host run.
        runner = SandboxedCommandRunner(self.root, mode="on",
                                        availability=lambda: False)
        report = VerificationPipeline(self.root, sandbox=runner).run()
        failed = [s for s in report.stages if s.status == "failed"]
        self.assertTrue(failed)
        self.assertEqual(failed[0].sandbox, "blocked")
        self.assertIn("docker is unavailable", failed[0].stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
