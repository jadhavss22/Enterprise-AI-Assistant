"""Explicit opt-in synthetic Gemini probe; prints metadata, never keys or bodies."""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chat.provider import ChatProviderError, GeminiProvider
from app.config import Settings


async def main():
    config = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env")
    provider = GeminiProvider(config)
    began = time.monotonic()
    count = characters = 0
    first_ms = None
    try:
        async for text in provider.stream(
            [
                {
                    "role": "system",
                    "content": "Answer only from the supplied synthetic evidence. Keep your answer one short sentence.",
                },
                {
                    "role": "user",
                    "content": "Evidence: Cedar Demo provides 18 annual leave days. Question: How many annual leave days?",
                },
            ]
        ):
            if first_ms is None:
                first_ms = round((time.monotonic() - began) * 1000)
            count += 1
            characters += len(text)
    except ChatProviderError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": exc.code,
                    "upstream_status": exc.upstream_status,
                    "failure_type": exc.failure_type,
                    "retry_after": exc.retry_after,
                    "model": config.gemini_chat_model,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "model": config.gemini_chat_model,
                "content_deltas": count,
                "characters": characters,
                "first_content_ms": first_ms,
                "total_ms": round((time.monotonic() - began) * 1000),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
