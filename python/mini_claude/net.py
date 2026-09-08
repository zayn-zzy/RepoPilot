"""Network resilience for the LLM clients.

The most common real-world cause of ``APIConnectionError`` ("Connection
error.") is a broken or unwanted HTTP(S)_PROXY setting in the user's
shell: httpx honors it by default, and the SDK's own retries all go
through the same dead proxy. Every LLM call in RepoPilot therefore
retries once through a ``trust_env=False`` (direct-connection) client
when the primary fails to connect. If both fail, the raised error names
the base URL and the proxy variables that were detected — enough to
fix the environment in one look.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


@contextmanager
def _proxy_env_removed() -> Iterator[None]:
    """Temporarily remove every proxy variable from os.environ.

    The vendored httpx2 used by anthropic 1.4.0 honors environment
    proxies even when the client is built with trust_env=False (verified
    empirically: a dead HTTPS_PROXY still broke the 'direct' client), so
    the only reliable direct-connection mode is a proxy-free environment.
    The window is a single request; the original variables are restored
    afterwards. This is process-global — RepoPilot runs one LLM call at
    a time, so the brief window is safe."""
    saved = {k: os.environ.pop(k) for k in PROXY_ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _direct_factory_error(e: Exception) -> RuntimeError:
    return RuntimeError(f"could not build the direct-connection client: {e}")


def proxy_summary() -> str:
    """Which proxy variables are set in this process (values included —
    they are local proxy addresses, not credentials)."""
    found = {k: v for k, v in os.environ.items() if k in PROXY_ENV_VARS}
    if not found:
        return "no HTTP(S)_PROXY variables are set in the environment"
    return "proxy variables detected: " + ", ".join(
        f"{k}={v}" for k, v in sorted(found.items()))


def _client_base_url(client: Any) -> str | None:
    return (getattr(client, "base_url", None)
            or getattr(client, "_base_url", None))


def describe_connection_error(base_url: str | None, error: Exception) -> str:
    url = base_url or "the default Anthropic endpoint"
    return "\n".join([
        f"could not connect to the LLM endpoint (base URL: {url}) "
        f"after retrying without proxy — {error}",
        f"proxy state: {proxy_summary()}",
        "fixes:",
        "  - if a proxy is set but not needed: unset HTTP_PROXY/HTTPS_PROXY",
        "    (temporarily: `unset HTTP_PROXY HTTPS_PROXY`),",
        "  - if outbound traffic requires a proxy: export the working one as",
        "    HTTPS_PROXY=http://<proxy-host>:<port>",
        "  - verify reachability: "
        f"curl -sS -o /dev/null -w '%{{http_code}}' {url}",
        "  - if the URL above is wrong, fix ANTHROPIC_BASE_URL in your .env",
    ])


async def anthropic_create_with_fallback(
    client: Any, direct_factory: Any | None, **params: Any,
) -> Any:
    """client.messages.create with one direct-connection retry.

    The retry client is built by ``direct_factory()`` INSIDE a
    proxy-variables-removed window: the vendored httpx2 bakes the
    environment's proxies into the transport at client CONSTRUCTION
    time, so removing the variables afterwards is not enough — the
    client itself must be created in a proxy-free environment."""
    import anthropic
    try:
        return await client.messages.create(**params)
    except anthropic.APIConnectionError as exc:
        if direct_factory is None:
            raise RuntimeError(
                describe_connection_error(_client_base_url(client), exc)) from exc
        try:
            with _proxy_env_removed():
                direct_client = direct_factory()
                try:
                    return await direct_client.messages.create(**params)
                except anthropic.APIConnectionError as exc2:
                    raise RuntimeError(
                        describe_connection_error(_client_base_url(client), exc2)
                    ) from exc2
        except RuntimeError:
            raise
        except Exception as exc3:  # factory construction failure
            raise _direct_factory_error(exc3) from exc3


def anthropic_create_sync_with_fallback(
    client: Any, direct_factory: Any | None, **params: Any,
) -> Any:
    """Sync variant (used by the CLI's LLM planner callable)."""
    import anthropic
    try:
        return client.messages.create(**params)
    except anthropic.APIConnectionError as exc:
        if direct_factory is None:
            raise RuntimeError(
                describe_connection_error(_client_base_url(client), exc)) from exc
        try:
            with _proxy_env_removed():
                direct_client = direct_factory()
                try:
                    return direct_client.messages.create(**params)
                except anthropic.APIConnectionError as exc2:
                    raise RuntimeError(
                        describe_connection_error(_client_base_url(client), exc2)
                    ) from exc2
        except RuntimeError:
            raise
        except Exception as exc3:
            raise _direct_factory_error(exc3) from exc3


@asynccontextmanager
async def anthropic_stream_with_fallback(
    client: Any, direct_factory: Any | None, **params: Any,
) -> AsyncIterator[Any]:
    """The streaming variant. Retried only when the connection fails at
    stream start (before any event was consumed); a mid-stream failure
    is re-raised untouched to avoid duplicated events. The retry client
    is built inside the proxy-free window (the transport bakes proxies
    at construction time)."""
    import anthropic
    yielded = False
    try:
        async with client.messages.stream(**params) as stream:
            yielded = True
            yield stream
    except anthropic.APIConnectionError as exc:
        if yielded or direct_factory is None:
            if yielded:
                raise
            raise RuntimeError(
                describe_connection_error(_client_base_url(client), exc)) from exc
        try:
            with _proxy_env_removed():
                direct_client = direct_factory()
                try:
                    async with direct_client.messages.stream(**params) as stream:
                        yield stream
                except anthropic.APIConnectionError as exc2:
                    raise RuntimeError(
                        describe_connection_error(_client_base_url(client), exc2)
                    ) from exc2
        except RuntimeError:
            raise
        except Exception as exc3:
            raise _direct_factory_error(exc3) from exc3
