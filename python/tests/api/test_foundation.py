"""FastAPI foundation tests (Web Phase 2): envelope contract, health/
ready, OpenAPI, request-id, CORS, and the no-traceback error handlers."""

import sys
import tempfile
import unittest
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from mini_claude.api.main import create_app  # noqa: E402
from mini_claude.api.config import Settings  # noqa: E402


def _app(tmp: str, *, workspace_root: Path | None = None,
         origins: list[str] | None = None):
    root = workspace_root or Path(tmp)
    return create_app(Settings(
        workspace_root=root,
        db_url=str(root / ".repopilot" / "app.db"),
        cors_origins=origins or ["http://localhost:5173"],
    ))


class TestHealth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = TestClient(_app(self._tmp.name))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_health_200_with_envelope(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["status"], "ok")
        self.assertIn("version", body["meta"])
        self.assertTrue(body["request_id"])

    def test_ready_200_when_workspace_and_db_ok(self):
        resp = self.client.get("/ready")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "ready")

    def test_ready_503_with_honest_reasons(self):
        client = TestClient(_app(
            self._tmp.name,
            workspace_root=Path(self._tmp.name) / "missing"))
        client.__enter__()
        try:
            resp = client.get("/ready")
            self.assertEqual(resp.status_code, 503)
            self.assertIn("workspace root does not exist",
                          str(resp.json()["data"]["reasons"]))
        finally:
            client.__exit__(None, None, None)

    def test_request_id_echoed_and_generated(self):
        resp = self.client.get("/health",
                               headers={"X-Request-ID": "req-abc"})
        self.assertEqual(resp.headers["X-Request-ID"], "req-abc")
        self.assertEqual(resp.json()["request_id"], "req-abc")
        resp = self.client.get("/health")
        self.assertTrue(resp.headers["X-Request-ID"])
        self.assertEqual(resp.json()["request_id"],
                         resp.headers["X-Request-ID"])

    def test_cors_preflight(self):
        resp = self.client.options(
            "/health",
            headers={"Origin": "http://localhost:5173",
                     "Access-Control-Request-Method": "GET"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"),
                         "http://localhost:5173")
        # a non-allowlisted origin gets no CORS grant
        resp = self.client.options(
            "/health",
            headers={"Origin": "http://evil.example",
                     "Access-Control-Request-Method": "GET"})
        self.assertIsNone(resp.headers.get("access-control-allow-origin"))

    def test_openapi_json(self):
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        spec = resp.json()
        self.assertEqual(spec["info"]["title"], "RepoPilot API")
        self.assertIn("/health", spec["paths"])


class TestErrorEnvelope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from mini_claude.application import ApplicationError
        app = _app(self._tmp.name)
        from fastapi import APIRouter
        test = APIRouter()

        @test.get("/boom-app")
        def _boom_app():
            raise ApplicationError("TEST_CODE", "test failure",
                                   details={"k": "v"})

        @test.get("/boom-raw")
        def _boom_raw():
            raise RuntimeError("secret internal detail")

        app.include_router(test)
        # raise_server_exceptions=False: the 500 envelope is what we test
        # (the default TestClient re-raises server exceptions instead).
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_application_error_envelope(self):
        resp = self.client.get("/boom-app")
        self.assertEqual(resp.status_code, 500)  # unmapped code → 500
        body = resp.json()
        self.assertEqual(body["error"]["code"], "TEST_CODE")
        self.assertEqual(body["error"]["details"], {"k": "v"})
        self.assertNotIn("data", body)
        self.assertTrue(body["request_id"])

    def test_application_error_status_mapping(self):
        from mini_claude.application import ApplicationError
        # not-found codes map to 404 via the handler
        from fastapi import APIRouter
        app = _app(self._tmp.name)
        test = APIRouter()

        @test.get("/boom-notfound")
        def _boom_notfound():
            raise ApplicationError("REPOSITORY_NOT_FOUND", "no such repo")

        app.include_router(test)
        client = TestClient(app)
        client.__enter__()
        try:
            resp = client.get("/boom-notfound")
            self.assertEqual(resp.status_code, 404)
            self.assertEqual(resp.json()["error"]["code"],
                             "REPOSITORY_NOT_FOUND")
        finally:
            client.__exit__(None, None, None)

    def test_unexpected_error_never_leaks_traceback(self):
        resp = self.client.get("/boom-raw")
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("Traceback", resp.text)
        self.assertNotIn("secret internal detail", resp.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
