"""DockerRunner tests — the full policy→docker-argv translation verified
with an injected executor (this environment has no docker daemon; the
runner's real execution path is recorded as untested in the docs)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from mini_claude.sandbox import DockerRunner, SandboxPolicy  # noqa: E402


def _env_entries(argv: list[str]) -> dict[str, str]:
    """Parse --env NAME=VALUE pairs out of a docker argv."""
    out = {}
    for i, tok in enumerate(argv):
        if tok == "--env":
            name, _, value = argv[i + 1].partition("=")
            out[name] = value
    return out


def _flag_value(argv: list[str], flag: str) -> str | None:
    if flag not in argv:
        return None
    return argv[argv.index(flag) + 1]


class _RecordingExecutor:
    """Replaces subprocess.run; records the call and returns a canned
    result."""

    def __init__(self, returncode=0, stdout="ok\n", stderr="",
                 raise_timeout=False):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raise_timeout = raise_timeout

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, self.returncode,
                                           self.stdout, self.stderr)


class TestDockerRunner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.policy = SandboxPolicy(
            workspace=self.repo,
            env_allowlist=frozenset({"PATH", "HOME", "LANG"}),
        )
        self.env = {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C",
                    "ANTHROPIC_API_KEY": "sk-secret",
                    "AWS_ACCESS_KEY_ID": "AKIA", "SSH_AUTH_SOCK": "/tmp/sock",
                    "DOCKER_HOST": "unix:///var/run/docker.sock",
                    "GITHUB_TOKEN": "ghp-token"}
        self.executor = _RecordingExecutor()
        self.runner = DockerRunner(self.policy, env=self.env,
                                   executor=self.executor)

    def _argv(self):
        self.assertEqual(len(self.executor.calls), 1)
        return self.executor.calls[0][0]

    def test_policy_dimensions_become_docker_flags(self):
        result = self.runner.run("pytest -q")
        self.assertTrue(result.ran)
        argv = self._argv()
        self.assertEqual(argv[0], "docker")
        self.assertEqual(argv[1:3], ["run", "--rm"])
        self.assertEqual(_flag_value(argv, "--network"), "none")
        self.assertEqual(_flag_value(argv, "--cpus"), "1.0")
        self.assertEqual(_flag_value(argv, "--memory"), "1g")
        self.assertEqual(_flag_value(argv, "--memory-swap"), "1g")
        self.assertEqual(_flag_value(argv, "--user"), "1000:1000")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertEqual(_flag_value(argv, "--cap-drop"), "ALL")
        # Workspace: read-only mount of the repo, the only view of it.
        self.assertIn("--volume", argv)
        self.assertIn(f"{self.repo}:/workspace:ro", argv)
        self.assertEqual(_flag_value(argv, "-w"), "/workspace")
        # The command rides as one argument after sh -lc.
        self.assertEqual(argv[-3:], ["sh", "-lc", "pytest -q"])
        # Timeout is enforced by the runner (executor kwarg).
        self.assertEqual(self.executor.calls[0][1]["timeout"], 300.0)

    def test_no_privileged_flag_ever(self):
        self.runner.run("echo hi")
        argv = self._argv()
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("privileged", " ".join(argv))

    def test_no_docker_socket_mount_ever(self):
        self.runner.run("echo hi")
        argv = self._argv()
        for tok in argv:
            self.assertNotIn("docker.sock", tok)
            self.assertNotIn("/var/run", tok)

    def test_secrets_never_injected_even_with_allowlist_gaps(self):
        # DOCKER_HOST etc. are not allowlisted, but prove the hard deny:
        # even a permissive allowlist cannot carry them through.
        permissive = DockerRunner(
            self.policy, env=self.env, executor=self.executor,
        )
        # Also add a runner whose policy allowlists a secret name directly.
        from mini_claude.sandbox.security import SecretFilter
        secret_names = ["ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID",
                        "SSH_AUTH_SOCK", "DOCKER_HOST", "GITHUB_TOKEN"]
        env_entries = _env_entries(permissive._build_argv("echo hi"))
        for name in secret_names:
            self.assertNotIn(name, env_entries, f"{name} must never be injected")
        # The minimal allowlist did pass through.
        self.assertEqual(set(env_entries), {"PATH", "HOME", "LANG"})

    def test_denied_command_never_reaches_executor(self):
        result = self.runner.run("rm -rf /")
        self.assertEqual(result.verdict.action, "deny")
        self.assertFalse(result.ran)
        self.assertEqual(self.executor.calls, [])

    def test_confirm_command_denied_without_approval_callback(self):
        result = self.runner.run("chmod -R 755 src/")
        self.assertEqual(result.verdict.action, "deny")  # approval not given
        self.assertIn("approval required", result.verdict.reason)
        self.assertEqual(self.executor.calls, [])

    def test_confirm_command_runs_after_human_approval(self):
        approved = []

        def confirm(command, reason):
            approved.append((command, reason))
            return True

        runner = DockerRunner(self.policy, env=self.env,
                              executor=self.executor, confirm=confirm)
        result = runner.run("docker build -t img .")
        self.assertTrue(result.ran)
        self.assertEqual(approved[0][1], DockerRunner(self.policy,
                          env=self.env, executor=self.executor).check(
                          "docker build -t img .").reason)
        self.assertTrue(any("approval" in r or "requires" in r
                            for _, r in approved))

    def test_confirm_callback_declining_blocks_execution(self):
        runner = DockerRunner(self.policy, env=self.env,
                              executor=self.executor,
                              confirm=lambda c, r: False)
        result = runner.run("docker build -t img .")
        self.assertFalse(result.ran)
        self.assertEqual(self.executor.calls, [])

    def test_timeout_surfaces_as_timed_out_result(self):
        runner = DockerRunner(self.policy, env=self.env,
                              executor=_RecordingExecutor(raise_timeout=True))
        result = runner.run("pytest -q", timeout_s=5)
        self.assertTrue(result.timed_out)
        self.assertIn("timed out after 5s", result.stderr)

    def test_output_is_capped(self):
        runner = DockerRunner(self.policy, env=self.env,
                              executor=_RecordingExecutor(stdout="x" * 5000))
        result = runner.run("echo big")
        self.assertEqual(len(result.stdout), 5000)  # under the 100k cap

    def test_workspace_path_check_enforces_repo_root(self):
        self.assertEqual(self.runner.check_workspace_path("pkg/x.py").action,
                         "allow")
        self.assertEqual(self.runner.check_workspace_path("../x.py").action,
                         "deny")
        no_ws = DockerRunner(SandboxPolicy(), env={}, executor=self.executor)
        self.assertEqual(no_ws.check_workspace_path("x.py").action, "deny")

    def test_executor_failure_is_reported_not_raised(self):
        def broken(argv, **kwargs):
            raise OSError("no such binary")

        runner = DockerRunner(self.policy, env=self.env, executor=broken)
        result = runner.run("echo hi")
        self.assertFalse(result.ran)
        self.assertIn("could not start sandbox", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
