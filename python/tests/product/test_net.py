"""net.py tests — the proxy-bypass fallback and the actionable
connection-error diagnostics (the fix for 'Connection error.')."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_DIR))

import anthropic  # noqa: E402

from mini_claude.net import (  # noqa: E402
    anthropic_create_sync_with_fallback,
    anthropic_create_with_fallback,
    anthropic_stream_with_fallback,
    describe_connection_error,
    proxy_summary,
)


class _Messages:
    """Fake messages API: sync or async handler, one shared `create`."""

    def __init__(self, fn, *, sync: bool = False):
        self._fn = fn
        self._sync = sync
        self.calls = 0

    def _invoke(self, params):
        self.calls += 1
        return self._fn(**params)

    async def create(self, **params):
        result = self._invoke(params)
        if self._sync:
            return result
        import inspect
        return await result if inspect.isawaitable(result) else result


def _conn_error():
    return anthropic.APIConnectionError(request=object())


class TestProxySummary(unittest.TestCase):
    def test_summary_format(self):
        # Either state is valid — the message must be informative. (This
        # sandbox itself sets proxy variables, so the assertion covers
        # both the detected and the clean cases.)
        msg = proxy_summary()
        self.assertTrue(
            msg == "no HTTP(S)_PROXY variables are set in the environment"
            or msg.startswith("proxy variables detected: "))

    def test_proxy_detected(self):
        import os
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9999"
        try:
            self.assertIn("HTTPS_PROXY=http://127.0.0.1:9999", proxy_summary())
        finally:
            os.environ.pop("HTTPS_PROXY")

    def test_describe_is_actionable(self):
        msg = describe_connection_error("https://api.deepseek.com/anthropic",
                                        _conn_error())
        for needle in ("base URL: https://api.deepseek.com/anthropic",
                       "unset HTTP_PROXY/HTTPS_PROXY",
                       "ANTHROPIC_BASE_URL", "curl -sS"):
            self.assertIn(needle, msg)


class TestFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.primary = SimpleNamespace(messages=_Messages(
            lambda **p: (_ for _ in ()).throw(_conn_error())))
        self.direct = SimpleNamespace(messages=_Messages(
            lambda **p: SimpleNamespace(content=[SimpleNamespace(type="text",
                                                                 text="OK")])))

    async def test_create_retries_direct_on_connection_error(self):
        out = await anthropic_create_with_fallback(
            self.primary, lambda: self.direct, model="m")
        self.assertEqual(out.content[0].text, "OK")
        self.assertEqual(self.primary.messages.calls, 1)
        self.assertEqual(self.direct.messages.calls, 1)

    async def test_create_passes_primary_when_it_works(self):
        ok = SimpleNamespace(messages=_Messages(
            lambda **p: SimpleNamespace(content=[SimpleNamespace(type="text",
                                                                 text="first")])))
        out = await anthropic_create_with_fallback(ok, lambda: self.direct, model="m")
        self.assertEqual(out.content[0].text, "first")
        self.assertEqual(self.direct.messages.calls, 0)

    async def test_both_fail_raises_descriptive_error(self):
        bad = SimpleNamespace(messages=_Messages(
            lambda **p: (_ for _ in ()).throw(_conn_error())))
        with self.assertRaises(RuntimeError) as ctx:
            await anthropic_create_with_fallback(self.primary, lambda: bad, model="m")
        self.assertIn("could not connect to the LLM endpoint",
                      str(ctx.exception))
        self.assertIn("proxy state:", str(ctx.exception))

    async def test_sync_fallback(self):
        class _SyncMessages:
            """The sync fallback calls a plain (non-async) create."""

            def __init__(self, fn):
                self._fn = fn
                self.calls = 0

            def create(self, **params):
                self.calls += 1
                return self._fn(**params)

        primary = SimpleNamespace(messages=_SyncMessages(
            lambda **p: (_ for _ in ()).throw(_conn_error())))
        direct = SimpleNamespace(messages=_SyncMessages(
            lambda **p: SimpleNamespace(content=[SimpleNamespace(type="text",
                                                                 text="sync-ok")])))
        out = anthropic_create_sync_with_fallback(primary, lambda: direct, model="m")
        self.assertEqual(out.content[0].text, "sync-ok")
        self.assertEqual(direct.messages.calls, 1)

    async def test_stream_fallback_on_connection_error(self):
        class _Stream:
            def __init__(self, text):
                self._events = [SimpleNamespace(type="content_block_delta",
                                                index=0,
                                                delta=SimpleNamespace(text=text)),
                                SimpleNamespace(type="message_stop")]
                self.content = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._events:
                    return self._events.pop(0)
                raise StopAsyncIteration

        class _StreamManager:
            def __init__(self, fail):
                self._fail = fail
                self.text = "direct"

            async def __aenter__(self):
                if self._fail:
                    raise _conn_error()
                return _Stream(self.text)

            async def __aexit__(self, *a):
                return False

        class _Msg:
            def __init__(self, fail):
                self._fail = fail
                self.calls = 0

            def stream(self, **params):
                self.calls += 1
                return _StreamManager(self._fail)

        primary = SimpleNamespace(messages=_Msg(fail=True))
        direct = SimpleNamespace(messages=_Msg(fail=False))
        got = []
        async with anthropic_stream_with_fallback(primary, lambda: direct,
                                                  model="m") as stream:
            async for event in stream:
                got.append(event.type)
        self.assertEqual(got, ["content_block_delta", "message_stop"])
        self.assertEqual(primary.messages.calls, 1)
        self.assertEqual(direct.messages.calls, 1)

    async def test_proxy_vars_removed_during_fallback_and_restored(self):
        import os
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"

        seen = []
        build_seen = []

        def handler(**p):
            seen.append(os.environ.get("HTTPS_PROXY"))  # must be absent here
            return SimpleNamespace(content=[SimpleNamespace(type="text",
                                                             text="direct-ok")])

        def factory():
            build_seen.append(os.environ.get("HTTPS_PROXY"))  # built proxy-free
            return SimpleNamespace(messages=_Messages(handler))

        primary = SimpleNamespace(messages=_Messages(
            lambda **p: (_ for _ in ()).throw(_conn_error())))
        out = await anthropic_create_with_fallback(primary, factory, model="m")
        self.assertEqual(out.content[0].text, "direct-ok")
        self.assertEqual(build_seen, [None])     # factory ran proxy-free
        self.assertEqual(seen, [None])           # call ran proxy-free too
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:1")  # restored

    async def test_stream_midstream_failure_is_not_retried(self):
        import mini_claude.net as net

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise _conn_error()

        class _Mgr:
            async def __aenter__(self):
                return _Stream()

            async def __aexit__(self, *a):
                return False

        class _Msg:
            def stream(self, **params):
                return _Mgr()

        primary = SimpleNamespace(messages=_Msg())
        with self.assertRaises(anthropic.APIConnectionError):
            async with anthropic_stream_with_fallback(primary, None,
                                                      model="m") as stream:
                async for _ in stream:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
