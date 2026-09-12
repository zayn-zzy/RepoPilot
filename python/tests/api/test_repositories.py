"""Repository API tests (Web Phase 3) — the §31 matrix: valid repo,
invalid (non-git) repo, outside workspace root, path traversal, index,
reindex, logical delete."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from mini_claude.api.main import create_app  # noqa: E402
from mini_claude.api.config import Settings  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


class TestRepositoryApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.workspace = tmp / "workspace"
        self.workspace.mkdir()
        self.repo = self.workspace / "calc"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "dev@test")
        _git(self.repo, "config", "user.name", "dev")
        (self.repo / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n"
            "    return a * b\n")
        (self.repo / "stats.py").write_text(
            "from calc import add\n\n\ndef total(xs):\n"
            "    return add(sum(xs), 0)\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "checkout", "-qb", "main")
        app = create_app(Settings(
            workspace_root=self.workspace,
            db_url=str(self.workspace / ".repopilot" / "app.db"),
            cors_origins=[]))
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def _register(self) -> str:
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(self.repo)})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["data"]["id"]

    def test_register_list_detail(self):
        repo_id = self._register()
        resp = self.client.get("/api/v1/repositories")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], repo_id)
        self.assertEqual(rows[0]["name"], "calc")
        resp = self.client.get(f"/api/v1/repositories/{repo_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["path"], str(self.repo))

    def test_invalid_non_git_repo_refused(self):
        plain = self.workspace / "plain"
        plain.mkdir()
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(plain)})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "NOT_A_GIT_REPOSITORY")

    def test_outside_workspace_root_refused(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(outside)})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"],
                         "PATH_OUTSIDE_WORKSPACE")

    def test_path_traversal_refused(self):
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(self.workspace / "..")})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"],
                         "PATH_OUTSIDE_WORKSPACE")

    def test_duplicate_path_conflicts(self):
        self._register()
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(self.repo)})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "ALREADY_EXISTS")

    def test_index_and_reindex_flow(self):
        repo_id = self._register()
        # initialize writes the config
        resp = self.client.post(f"/api/v1/repositories/{repo_id}/initialize")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["ok"])
        # index → fresh build
        resp = self.client.post(f"/api/v1/repositories/{repo_id}/index",
                                json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["files"], 2)
        self.assertIn("built fresh", data["note"])
        # status endpoint reflects it
        resp = self.client.get(f"/api/v1/repositories/{repo_id}/index")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["exists"])
        self.assertEqual(resp.json()["data"]["files"], 2)
        # reindex → loaded, up to date (persistence reused)
        resp = self.client.post(f"/api/v1/repositories/{repo_id}/index",
                                json={})
        self.assertIn("up to date", resp.json()["data"]["note"])
        # the repository record carries the index summary
        resp = self.client.get(f"/api/v1/repositories/{repo_id}")
        row = resp.json()["data"]
        self.assertEqual(row["index_files"], 2)
        self.assertIsNotNone(row["last_indexed_at"])

    def test_delete_is_logical_and_detail_404(self):
        repo_id = self._register()
        resp = self.client.delete(f"/api/v1/repositories/{repo_id}")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(f"/api/v1/repositories/{repo_id}")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "REPOSITORY_NOT_FOUND")
        # gone from the list too
        resp = self.client.get("/api/v1/repositories")
        self.assertEqual(resp.json()["data"], [])
        # the files on disk are untouched by the logical delete
        self.assertTrue((self.repo / "calc.py").exists())

    def test_unknown_id_404(self):
        resp = self.client.get("/api/v1/repositories/nope")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
