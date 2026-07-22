"""LLM access layer: a single place that owns the OpenAI client and structured calls.

- Model names come from config (`llm_model_strong` / `llm_model_fast`), never hardcoded.
- **Langfuse tracing is wired here, from the first call** — but it's optional: when
  Langfuse keys are set we use its drop-in traced OpenAI client, otherwise a plain one.
  Tracing must never be a hard dependency, so any Langfuse setup failure degrades to the
  plain client rather than breaking the app.
- `complete_json` does one structured-output call and returns parsed JSON. Callers pass
  a JSON schema so the model's output shape is guaranteed.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings


class LLMError(Exception):
    """LLM call failed or returned unusable output."""


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
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

            return TracedAsyncOpenAI(api_key=settings.openai_api_key)
        except Exception:
            # Tracing is best-effort; never let it stop real work.
            pass
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def complete_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str = "response",
) -> dict[str, Any]:
    """One structured-output chat call. Returns parsed JSON matching `schema`.

    Raises `LLMError` if the key is missing, the call fails, or the output isn't valid
    JSON — callers turn that into a clear ingestion failure, never a 500.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not set")

    try:
        resp = await get_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        )
    except Exception as exc:  # openai errors, network, etc.
        raise LLMError(f"LLM request failed: {exc}") from exc

    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMError(f"model did not return valid JSON: {exc}") from exc
