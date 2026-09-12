"""Ask + Plan API tests (Web Phase 4): structured retrieval evidence
(honest per-source scores), structured plan nodes/edges persisted and
readable back — never terminal strings wrapped in JSON (§32)."""

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


class TestAskPlanApi(unittest.TestCase):
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
            "    return a * b\n\n# pricing: 订单总价在这里计算\n")
        (self.repo / "stats.py").write_text(
            "from calc import add\n\n\ndef total(xs):\n"
            "    return add(sum(xs), 0)\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "checkout", "-qb", "main")
        # index via the API so ask has an index to reuse
        app = create_app(Settings(
            workspace_root=self.workspace,
            db_url=str(self.workspace / ".repopilot" / "app.db"),
            cors_origins=[]))
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(self.repo)})
        self.repo_id = resp.json()["data"]["id"]
        self.client.post(f"/api/v1/repositories/{self.repo_id}/index", json={})

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_ask_returns_structured_evidence(self):
        resp = self.client.post(
            f"/api/v1/repositories/{self.repo_id}/ask",
            json={"question": "multiply 在哪里定义", "semantic": "lsa",
                  "answer": False})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertIn("lsa", data["semantic_label"])
        self.assertTrue(data["context"])
        self.assertTrue(data["hits"])
        hit = data["hits"][0]
        self.assertEqual(set(hit), {"file_path", "score", "sources"})
        self.assertTrue(hit["file_path"].endswith("calc.py"))
        self.assertIn("semantic", hit["sources"])   # real per-source scores
        self.assertFalse(data["has_answer"])        # no key → honest

    def test_plan_create_and_get_roundtrip(self):
        resp = self.client.post(
            f"/api/v1/repositories/{self.repo_id}/plans",
            json={"requirement": "fix the multiply bug in calc.py"})
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["planner"], "deterministic")
        self.assertTrue(data["nodes"])
        first = data["nodes"][0]
        self.assertEqual(first["agent_role"], "coder")
        self.assertEqual(set(first), {"id", "agent_role", "title",
                                      "description", "dependencies",
                                      "priority", "files", "budget"})
        self.assertEqual(data["repository_id"], self.repo_id)
        # edges reference node ids only
        node_ids = {n["id"] for n in data["nodes"]}
        for edge in data["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)

        resp = self.client.get(f"/api/v1/plans/{data['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["nodes"], data["nodes"])

    def test_llm_plan_without_key_is_an_api_key_error(self):
        import os
        saved = {k: os.environ.pop(k) for k in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if k in os.environ}
        try:
            resp = self.client.post(
                f"/api/v1/repositories/{self.repo_id}/plans",
                json={"requirement": "x", "llm": True})
        finally:
            os.environ.update(saved)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "API_KEY_REQUIRED")

    def test_plan_unknown_id_404(self):
        resp = self.client.get("/api/v1/plans/nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "PLAN_NOT_FOUND")

    def test_ask_unknown_repo_404(self):
        resp = self.client.post(
            "/api/v1/repositories/nope/ask",
            json={"question": "x", "answer": False})
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
