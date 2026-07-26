"""Smoke test for the LLM layer — confirms your key + base_url + model + structured
output all work, without running the whole ingestion pipeline.

Run from backend/ with your .env in place:

    uv run python check_llm.py

Success prints the config it used and a parsed JSON object. Any failure prints a
clear reason (missing key, wrong model name, or the provider rejecting the JSON
schema). Safe to delete once you've confirmed things work.
"""

import asyncio

from app.config import get_settings
from app.llm import LLMError, complete_json


async def main() -> None:
    s = get_settings()
    print(f"base_url : {s.llm_base_url or '(OpenAI default)'}")
    print(f"model    : {s.llm_model_strong}")
    print(f"key set  : {'yes' if s.openai_api_key else 'NO — set OPENAI_API_KEY in .env'}")
    print("-" * 40)

    try:
        result = await complete_json(
            model=s.llm_model_strong,
            system="You are a helpful assistant. Reply only in JSON.",
            user="Return a short greeting and the integer 7.",
            schema={
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "number": {"type": "integer"},
                },
                "required": ["greeting", "number"],
                "additionalProperties": False,
            },
            schema_name="smoke",
        )
    except LLMError as exc:
        print(f"FAILED: {exc}")
        return

    print(f"OK — provider returned valid structured JSON:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
