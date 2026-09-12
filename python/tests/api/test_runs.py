"""Run worker + event tests (Web Phase 5) — the §33 matrix: create run
→ 202, the embedded worker consumes it, events persist, SSE delivers
them, the run completes, cancel is real (task-boundary, never cosmetic)
and retry links the original. The executor's run implementation is
scripted (no LLM); the pipeline around it is the real one."""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from mini_claude.api.main import create_app  # noqa: E402
from mini_claude.api.config import Settings  # noqa: E402
from mini_claude.worker.executor import RunExecutor  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _make_fake_run_factory(delay: float = 0.3):
    """A scripted run implementation: emits task events, sleeps, and
    returns a completed report — the executor pipeline around it is
    real (events, persistence, status transitions, SSE)."""
    async def fake_run(path, run, api_key, sink, cancel_check, plan):
        sink({"event_type": "task.started", "task_id": "T-B-1",
              "agent_id": "coder", "status": None, "message": "",
              "payload": {}})
        await asyncio.sleep(delay)
        if cancel_check():
            return SimpleNamespace(report=SimpleNamespace(
                final_verification=None, diff=None, success=False,
                outcomes=[], note="cancelled", pr=None))
        sink({"event_type": "verification.passed", "task_id": "T-B-1",
              "agent_id": "coder", "status": None,
              "message": "3 passed", "payload": {}})
        sink({"event_type": "task.completed", "task_id": "T-B-1",
              "agent_id": "coder", "status": "succeeded", "message": "",
              "payload": {}})
        return SimpleNamespace(report=SimpleNamespace(
            final_verification={"passed": True, "tests_passed": 3,
                                "tests_total": 3, "summary": "3 passed"},
            diff=SimpleNamespace(all_files=["calc.py"]),
            success=True, outcomes=[SimpleNamespace(cost_usd=0.05)],
            note="", pr=SimpleNamespace(title="repopilot: fix")))
    return fake_run


class TestRunWorkerEvents(unittest.TestCase):
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
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "checkout", "-qb", "main")

        def executor_factory(settings, factory, publisher, cancels):
            return RunExecutor(settings, factory, publisher, cancels,
                               run_factory=_make_fake_run_factory())
        self._executor_factory = executor_factory
        app = create_app(Settings(
            workspace_root=self.workspace,
            db_url=str(self.workspace / ".repopilot" / "app.db"),
            cors_origins=[]), executor_factory=executor_factory)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        resp = self.client.post("/api/v1/repositories",
                                json={"path": str(self.repo)})
        self.repo_id = resp.json()["data"]["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def _create_run(self, requirement="fix the multiply bug",
                    **body) -> dict:
        resp = self.client.post(
            f"/api/v1/repositories/{self.repo_id}/runs",
            json={"requirement": requirement, **body})
        self.assertEqual(resp.status_code, 202, resp.text)
        return resp.json()["data"]

    def _wait_status(self, run_id: str, statuses: tuple,
                     timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.client.get(f"/api/v1/runs/{run_id}").json()["data"]
            if data["status"] in statuses:
                return data
            time.sleep(0.2)
        raise AssertionError(f"run {run_id} never reached {statuses}: {data}")

    def test_create_run_202_then_worker_completes_it(self):
        created = self._create_run()
        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["bus"], "in-process")
        run_id = created["run_id"]
        data = self._wait_status(run_id, ("completed", "failed"))
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["verification"]["tests_passed"], 3)
        self.assertEqual(data["diff"]["files"], ["calc.py"])
        self.assertGreater(data["cost_usd"], 0)
        self.assertIsNotNone(data["started_at"])
        self.assertIsNotNone(data["finished_at"])
        # the events persisted: created/queued/started/plan/task/
        # verification/file.changed/completed
        resp = self.client.get(f"/api/v1/runs/{run_id}")
        count = resp.json()["meta"]["events"]
        self.assertGreaterEqual(count, 8, resp.json())

    def test_events_replayed_with_types(self):
        run_id = self._create_run()["run_id"]
        self._wait_status(run_id, ("completed",))
        from mini_claude.events.store import EventStore
        from mini_claude.persistence.database import session_factory
        store = EventStore(session_factory(
            str(self.workspace / ".repopilot" / "app.db")))
        events = store.replay(run_id)
        types = [e["event_type"] for e in events]
        for expected in ("run.created", "run.queued", "run.started",
                         "plan.started", "plan.completed", "task.started",
                         "verification.passed", "task.completed",
                         "file.changed", "run.completed"):
            self.assertIn(expected, types, types)
        # seq is strictly increasing — the one SSE id space
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_sse_streams_events_and_ends_when_terminal(self):
        run_id = self._create_run()["run_id"]
        with self.client.stream("GET",
                                f"/api/v1/runs/{run_id}/events") as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream",
                          resp.headers.get("content-type", ""))
            frames: list[tuple[str, str]] = []
            event_type = None
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    event_type = line[len("event: "):]
                elif line.startswith("data: "):
                    frames.append((event_type or "",
                                   json.loads(line[len("data: "):])["event_type"]))
            types = [t for _, t in frames]
            self.assertIn("run.created", types)
            self.assertIn("task.started", types)
            self.assertIn("run.completed", types)
        # after the terminal event the stream closed by itself
        data = self.client.get(f"/api/v1/runs/{run_id}").json()["data"]
        self.assertEqual(data["status"], "completed")

    def test_cancel_running_run_is_real(self):
        # a slow run so the cancel lands while it is RUNNING
        def slow_factory(settings, factory, publisher, cancels):
            return RunExecutor(settings, factory, publisher, cancels,
                               run_factory=_make_fake_run_factory(delay=5.0))
        # rebuild the app with the slow executor for THIS test only
        self.client.__exit__(None, None, None)
        app = create_app(Settings(
            workspace_root=self.workspace,
            db_url=str(self.workspace / ".repopilot" / "app.db"),
            cors_origins=[]), executor_factory=slow_factory)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        run_id = self._create_run()["run_id"]
        data = self._wait_status(run_id, ("running",))
        resp = self.client.post(f"/api/v1/runs/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = self.client.get(f"/api/v1/runs/{run_id}").json()["data"]
        self.assertEqual(data["status"], "cancelling")
        self.assertTrue(data["cancel_requested"])
        final = self._wait_status(run_id, ("cancelled",))
        self.assertEqual(final["status"], "cancelled")
        # a second cancel is refused honestly
        resp = self.client.post(f"/api/v1/runs/{run_id}/cancel")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "RUN_NOT_CANCELLABLE")

    def test_cancel_queued_run_is_final(self):
        # Deterministic queueing: a slow run occupies the single worker,
        # so the second run stays QUEUED — its cancel is final and the
        # executor skips it when the worker finally reaches it.
        def slow_factory(settings, factory, publisher, cancels):
            return RunExecutor(settings, factory, publisher, cancels,
                               run_factory=_make_fake_run_factory(delay=5.0))
        self.client.__exit__(None, None, None)
        app = create_app(Settings(
            workspace_root=self.workspace,
            db_url=str(self.workspace / ".repopilot" / "app.db"),
            cors_origins=[]), executor_factory=slow_factory)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        run_a = self._create_run()["run_id"]
        self._wait_status(run_a, ("running",))
        run_b = self._create_run()["run_id"]
        resp = self.client.post(f"/api/v1/runs/{run_b}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = self.client.get(f"/api/v1/runs/{run_b}").json()["data"]
        self.assertEqual(data["status"], "cancelled")
        self.assertIsNone(data["started_at"])   # never started
        self._wait_status(run_a, ("completed",))
        data = self.client.get(f"/api/v1/runs/{run_b}").json()["data"]
        self.assertEqual(data["status"], "cancelled")  # skipped, still cancelled
        self.assertIsNone(data["started_at"])

    def test_retry_creates_linked_run(self):
        run_id = self._create_run()["run_id"]
        self._wait_status(run_id, ("completed",))
        resp = self.client.post(f"/api/v1/runs/{run_id}/retry")
        self.assertEqual(resp.status_code, 202)
        retried = resp.json()["data"]
        self.assertEqual(retried["original_run_id"], run_id)
        self.assertNotEqual(retried["run_id"], run_id)
        self._wait_status(retried["run_id"], ("completed",))

    def test_unknown_run_404(self):
        resp = self.client.get("/api/v1/runs/nope")
        self.assertEqual(resp.status_code, 404)


class TestBusInProcess(unittest.TestCase):
    """The transport contract without Redis (dev/test mode)."""

    def test_job_push_read_ack_and_dead_letter(self):
        async def scenario():
            from mini_claude.worker.bus import InProcessBus
            bus = InProcessBus()
            await bus.push_job({"run_id": "r1"})
            job = await bus.read_job(block_ms=100)
            self.assertEqual(job.payload, {"run_id": "r1"})
            await bus.ack_job(job.job_id)
            await bus.push_job({"run_id": "r2"})
            job = await bus.read_job(block_ms=100)
            await bus.dead_letter(job.job_id, job.payload, "boom")
            self.assertEqual(bus._dead[-1]["error"], "boom")
            self.assertIsNone(await bus.read_job(block_ms=50))
        asyncio.run(scenario())

    def test_event_tail_by_seq(self):
        async def scenario():
            from mini_claude.worker.bus import InProcessBus
            bus = InProcessBus()
            for seq in range(1, 4):
                await bus.publish_event("r1", {"seq": seq,
                                               "event_type": "run.queued"})
            got = await bus.tail_events("r1", after_seq=1, block_ms=50)
            self.assertEqual([e["seq"] for e in got], [2, 3])
        asyncio.run(scenario())


def _redis_available() -> bool:
    import shutil
    return shutil.which("redis-server") is not None


@unittest.skipUnless(_redis_available(), "redis-server not installed")
class TestBusRedis(unittest.TestCase):
    """The transport contract against a REAL redis-server (spawned on a
    test port) — push/read/ack, crash recovery (XAUTOCLAIM), dead
    letter, event tail by seq."""

    PORT = 6397

    @classmethod
    def setUpClass(cls):
        import shutil
        cls._bin = shutil.which("redis-server")
        subprocess.run([cls._bin, "--port", str(cls.PORT), "--daemonize",
                        "yes", "--save", "", "--appendonly", "no"],
                       capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["redis-cli", "-p", str(cls.PORT), "shutdown",
                        "nosave"], capture_output=True)

    def test_job_lifecycle_and_crash_recovery(self):
        async def scenario():
            from mini_claude.worker.bus import RedisBus
            url = f"redis://127.0.0.1:{self.PORT}/0"
            bus = RedisBus(url=url, worker_id="worker-a")
            await bus.push_job({"run_id": "r1"})
            job = await bus.read_job(block_ms=2000)
            self.assertEqual(job.payload, {"run_id": "r1"})
            await bus.ack_job(job.job_id)
            self.assertIsNone(await bus.read_job(block_ms=300))
            # crash: read without ACK, a new worker reclaims
            await bus.push_job({"run_id": "r2"})
            await bus.read_job(block_ms=1000)
            await asyncio.sleep(0.2)
            bus2 = RedisBus(url=url, worker_id="worker-b")
            reclaimed = await bus2.reclaim_stale(min_idle_ms=50)
            self.assertEqual([j.payload for j in reclaimed],
                             [{"run_id": "r2"}])
            # dead letter keeps the payload inspectable
            await bus2.push_job({"run_id": "r3"})
            job3 = await bus2.read_job(block_ms=500)
            await bus2.dead_letter(job3.job_id, job3.payload, "boom")
            r = await bus2._redis()
            dead = await r.xrange("repopilot:dead")
            self.assertEqual(len(dead), 1)
            await bus.close()
            await bus2.close()
        asyncio.run(scenario())

    def test_event_tail_by_seq_on_redis(self):
        async def scenario():
            from mini_claude.worker.bus import RedisBus
            bus = RedisBus(url=f"redis://127.0.0.1:{self.PORT}/0")
            for seq in (1, 2, 3):
                await bus.publish_event("r1", {"seq": seq,
                                               "event_type": "task.started"})
            await asyncio.sleep(0.1)
            got = await bus.tail_events("r1", after_seq=1, block_ms=500)
            self.assertEqual([e["seq"] for e in got], [2, 3])
            await bus.close()
        asyncio.run(scenario())


class TestDagRunnerEventHooks(unittest.TestCase):
    """The DagRunner's event_sink emits real lifecycle events during a
    real (scripted-LLM) run — the live feed the Run Console shows."""

    def test_real_run_emits_task_and_verification_events(self):
        import asyncio as aio
        from mini_claude.execution import DagRunner
        from mini_claude.planning import TaskDAG
        from mini_claude.planning.task import TaskNode
        from mini_claude.application import PlanningService
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = _make_scripted_repo(Path(tmp.name))

        dag = TaskDAG([TaskNode(id="T001", title="fix multiply",
                                description="multiply must return a*b",
                                agent_role="coder")])
        events: list[dict] = []

        def after_build(runtime, task):
            runtime.agent._anthropic_client = SimpleNamespace(
                messages=_FakeMessages([
                    _tool_use_stream("read_file", {"file_path": "calc.py"}),
                    _tool_use_stream("write_file",
                                     {"file_path": "calc.py",
                                      "content": _FIXED_CALC}),
                    _text_stream("done"),
                ]))

        report = aio.run(DagRunner(
            repo, dag, task_id="EVT1", model="mock-model", api_key="k",
            jobs=1, sandbox="off", after_build=after_build,
            event_sink=events.append, commit=True).run(
                PlanningService().parse("fix the multiply bug")))
        self.assertTrue(report.success, report.summarize())
        types = [e["event_type"] for e in events]
        self.assertIn("task.started", types)
        self.assertIn("task.completed", types)
        self.assertIn("verification.passed", types)
        # task events carry the ids the UI keys on
        started = next(e for e in events if e["event_type"] == "task.started")
        self.assertEqual(started["task_id"], "T001")
        self.assertEqual(started["agent_id"], "coder")


def _make_scripted_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@test")
    _git(repo, "config", "user.name", "dev")
    (repo / "calc.py").write_text(_BUGGY_CALC)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_multiply():\n    assert multiply(3, 4) == 12\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "main")
    return repo


_BUGGY_CALC = ("def add(a, b):\n    return a + b\n\n\n"
               "def multiply(a, b):\n    return a + b\n")
_FIXED_CALC = ("def add(a, b):\n    return a + b\n\n\n"
               "def multiply(a, b):\n    return a * b\n")


class _FakeStream:
    def __init__(self, events, final_message):
        self._events = list(events)
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20,
                           cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)


def _text_stream(text: str):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(text=text)),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=_usage()))


def _tool_use_stream(name: str, inp: dict):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="t1",
                                                      name=name)),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(partial_json=json.dumps(inp))),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    return _FakeStream(events, SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name=name,
                                 input=dict(inp))],
        usage=_usage()))


class _FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def stream(self, **params):
        self.calls += 1
        return self._script.pop(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
