"""LLM access layer: a single place that owns the LLM client and structured calls.

- **Provider-agnostic by design.** We use the OpenAI SDK, but point it at any
  OpenAI-compatible endpoint via `llm_base_url` (Google Gemini free tier by default,
  or Groq, OpenRouter, a local Ollama, or OpenAI itself). Switching providers is a
  `.env` edit, not a code change — a deliberate "no vendor lock-in" decision.
- Model names come from config (`llm_model_strong` / `llm_model_fast`), never hardcoded.
- **Langfuse tracing is wired here, from the first call** — but it's optional: when
  Langfuse keys are set we use its drop-in traced OpenAI client, otherwise a plain one.
  Tracing must never be a hard dependency, so any Langfuse setup failure degrades to the
  plain client rather than breaking the app.
- `complete_json` does one structured-output call and returns parsed JSON. Callers pass
  a JSON schema so the model's output shape is guaranteed.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from typing import Any, TypeVar

import openai
from openai import AsyncOpenAI

from app.config import get_settings


class LLMError(Exception):
    """LLM call failed or returned unusable output."""


_T = TypeVar("_T")
_MAX_RETRIES = 4  # free-tier providers (Gemini: 5 req/min/model) 429 readily; back off + retry


def _retry_after_seconds(exc: openai.RateLimitError, attempt: int) -> float:
    """Honor the provider's suggested retry delay (Gemini returns `retryDelay: '58s'` in
    the 429 body); otherwise exponential backoff. Capped so we never hang too long."""
    match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)", str(exc))
    if match:
        return min(float(match.group(1)) + 1.0, 65.0)
    return min(2.0 * (attempt + 1) ** 2, 65.0)


async def _create_with_retry(make_call: Callable[[], Awaitable[_T]]) -> _T:
    """Run an LLM create-call, retrying on 429 with backoff. Safe for streaming too: the
    429 is raised before any token is yielded, so re-issuing the call loses nothing."""
    last: openai.RateLimitError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await make_call()
        except openai.RateLimitError as exc:
            last = exc
            if attempt == _MAX_RETRIES - 1:
                break
            await asyncio.sleep(_retry_after_seconds(exc, attempt))
    raise LLMError(f"rate-limited after {_MAX_RETRIES} attempts: {last}")


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    # base_url="" means the SDK's default (OpenAI). Any other value points the same SDK
    # at an OpenAI-compatible provider (Gemini/Groq/Ollama/…). None lets the SDK default.
    base_url = settings.llm_base_url or None
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            from langfuse import Langfuse

            # Initialize the Langfuse singleton so the drop-in client below traces.
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI

            return TracedAsyncOpenAI(api_key=settings.openai_api_key, base_url=base_url)
        except Exception:
            # Tracing is best-effort; never let it stop real work.
            pass
    return AsyncOpenAI(api_key=settings.openai_api_key, base_url=base_url)


async def complete_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str = "response",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """One structured-output chat call. Returns parsed JSON matching `schema`.

    `images` are `data:` URIs (base64) attached to the user message for multimodal
    calls (e.g. reading a problem screenshot). Raises `LLMError` if the key is missing,
    the call fails, or the output isn't valid JSON — callers turn that into a clear
    failure, never a 500.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise LLMError("LLM API key is not set (OPENAI_API_KEY in .env)")

    if images:
        user_content: Any = [{"type": "text", "text": user}]
        user_content += [{"type": "image_url", "image_url": {"url": uri}} for uri in images]
    else:
        user_content = user

    try:
        resp = await _create_with_retry(
            lambda: get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
            )
        )
    except LLMError:
        raise
    except Exception as exc:  # openai errors, network, etc.
        raise LLMError(f"LLM request failed: {exc}") from exc

    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMError(f"model did not return valid JSON: {exc}") from exc


async def stream_completion(
    *, model: str, messages: list[dict[str, Any]]
) -> AsyncIterator[str]:
    """Stream a chat completion as text deltas. Used by the tutor so multi-second replies
    type out live instead of hanging behind a spinner. `messages` is the full thread
    (system + history + new user turn). Raises `LLMError` on failure."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise LLMError("LLM API key is not set (OPENAI_API_KEY in .env)")
    try:
        stream = await _create_with_retry(
            lambda: get_client().chat.completions.create(
                model=model, messages=messages, stream=True
            )
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"LLM stream failed: {exc}") from exc


async def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed a batch of texts via the same provider-agnostic client (Gemini
    `gemini-embedding-001` by default). Returns one vector per input, order-preserved.

    Used by grounding ingestion (embed corpus chunks) and the tutor (embed the query for
    retrieval). Raises `LLMError` so callers never surface a raw provider error. The model
    is fixed per Qdrant collection — changing it means re-embedding everything, which is
    why the collection name encodes the model (see `Settings.grounding_collection`)."""
    if not texts:
        return []
    settings = get_settings()
    if not settings.openai_api_key:
        raise LLMError("LLM API key is not set (OPENAI_API_KEY in .env)")
    try:
        resp = await _create_with_retry(
            lambda: get_client().embeddings.create(
                model=model or settings.embedding_model,
                input=texts,
            )
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"embedding request failed: {exc}") from exc
    # OpenAI populates `index`; Gemini's compat layer leaves it None but returns in input
    # order. Sort by index when present, otherwise trust the response order.
    data = resp.data
    if all(d.index is not None for d in data):
        data = sorted(data, key=lambda d: d.index)
    return [d.embedding for d in data]
