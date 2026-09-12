"""Shared LLM plumbing for the Application Services (Web Phase 1).

Moved out of product/cli.py so the CLI, the services, and (later) the
FastAPI layer construct the exact same LLM calls — one implementation,
no CLI/API divergence (§2.1)."""

from __future__ import annotations

import os


def llm_base_url() -> str | None:
    """The Anthropic-compatible base URL. DeepSeek convenience: the bare
    OpenAI-compatible root (https://api.deepseek.com) returns 404 on the
    Anthropic protocol — normalize it to the /anthropic endpoint."""
    url = os.environ.get("ANTHROPIC_BASE_URL") or ""
    if url.rstrip("/") == "https://api.deepseek.com":
        return "https://api.deepseek.com/anthropic"
    return url or None


def make_llm_call(api_key: str, model: str):
    """The Planner's LLMCall contract: async (system, user) -> text.
    Uses the Anthropic SDK pointed at the DeepSeek-compatible endpoint
    (thinking must be disabled on SDK 1.4), with the net.py
    direct-connection fallback for broken proxies."""
    import anthropic
    from ..net import anthropic_create_sync_with_fallback  # noqa: PLC0415
    base_url = llm_base_url()
    client = anthropic.Anthropic(
        api_key=api_key, base_url=base_url, timeout=120)
    direct_factory = lambda: anthropic.Anthropic(  # noqa: E731
        api_key=api_key, base_url=base_url, timeout=120)

    async def llm_call(system: str, user: str) -> str:
        import asyncio
        response = await asyncio.to_thread(
            anthropic_create_sync_with_fallback,
            client, direct_factory,
            model=model,
            max_tokens=2000,
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content
                       if getattr(b, "type", "") == "text")

    return llm_call
