"""Gemini-only transport. Never log SDK exception bodies or user content."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.config import Settings, settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class ChatProviderError(Exception):
    code: str
    message: str
    retry_after: int | None = None
    upstream_status: int | None = None
    failure_type: str | None = None

    def __str__(self):
        return self.message


def retry_seconds(raw):
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not 0 <= seconds <= 3600:
        return None
    return max(1, int(seconds))


def safe_status_error(exc):
    status = exc.status_code
    if status == 429:
        return ChatProviderError(
            "CHAT_RATE_LIMITED",
            "AI provider quota reached; retry later",
            retry_seconds(exc.response.headers.get("retry-after")),
        )
    if status in (401, 403):
        return ChatProviderError(
            "CHAT_CONFIGURATION_ERROR",
            "AI provider credentials or permissions require administrator attention",
        )
    if status in (400, 404):
        return ChatProviderError(
            "CHAT_MODEL_UNAVAILABLE",
            "Configured AI model or request settings are unavailable",
        )
    return ChatProviderError(
        "CHAT_UNAVAILABLE", "AI provider is temporarily unavailable"
    )


class GeminiProvider:
    def __init__(self, config: Settings = settings, *, transport=None):
        self.config = config
        self.transport = transport

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens=None
    ) -> AsyncIterator[str]:
        if not self.config.gemini_api_key or not self.config.gemini_api_key.strip():
            raise ChatProviderError(
                "CHAT_NOT_CONFIGURED", "Gemini API key is not configured"
            )
        if not messages or sum(len(m.get("content", "")) for m in messages) > 48000:
            raise ChatProviderError(
                "CHAT_INPUT_INVALID", "AI request is empty or exceeds the input budget"
            )
        stream = None
        try:
            # Disable SDK retries: never silently replay a partially consumed generation.
            async with AsyncOpenAI(
                api_key=self.config.gemini_api_key,
                base_url=GEMINI_BASE_URL,
                max_retries=0,
                http_client=httpx.AsyncClient(
                    transport=self.transport,
                    timeout=httpx.Timeout(self.config.chat_timeout_seconds, connect=5),
                ),
            ) as client:
                deadline = time.monotonic() + self.config.chat_timeout_seconds
                first_deadline = min(
                    deadline,
                    time.monotonic() + self.config.chat_first_token_timeout_seconds,
                )
                stream = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=self.config.gemini_chat_model,
                        messages=messages,
                        stream=True,
                        reasoning_effort="low",
                        max_tokens=max_tokens or self.config.chat_max_completion_tokens,
                    ),
                    timeout=max(0, first_deadline - time.monotonic()),
                )
                iterator = stream.__aiter__()
                total = 0
                finished = False
                while True:
                    remaining = (
                        deadline if total else first_deadline
                    ) - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        chunk = await asyncio.wait_for(
                            anext(iterator), timeout=remaining
                        )
                    except StopAsyncIteration:
                        break
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    content = choice.delta.content
                    if content:
                        total += len(content)
                        if total > self.config.chat_max_output_characters:
                            raise ChatProviderError(
                                "CHAT_OUTPUT_LIMIT",
                                "AI response exceeded its output budget",
                            )
                        yield content
                    if choice.finish_reason:
                        if choice.finish_reason != "stop":
                            raise ChatProviderError(
                                "CHAT_INCOMPLETE",
                                "AI response was interrupted or reached its token limit",
                            )
                        finished = True
                        break
                if not finished:
                    raise ChatProviderError(
                        "CHAT_INCOMPLETE",
                        "AI response ended without successful completion",
                    )
                if not total:
                    raise ChatProviderError(
                        "CHAT_EMPTY_RESPONSE", "AI provider returned no answer"
                    )
        except ChatProviderError:
            raise
        except (TimeoutError, APITimeoutError):
            raise ChatProviderError(
                "CHAT_TIMEOUT", "AI response timed out; retry later"
            ) from None
        except APIStatusError as exc:
            error = safe_status_error(exc)
            error.upstream_status = exc.status_code
            error.failure_type = type(exc).__name__
            raise error from None
        except (APIConnectionError, httpx.HTTPError) as exc:
            raise ChatProviderError(
                "CHAT_UNAVAILABLE",
                "Cannot reach the AI provider; retry later",
                failure_type=type(exc).__name__,
            ) from None
        except Exception as exc:  # noqa: BLE001 - sanitize malformed provider responses
            raise ChatProviderError(
                "CHAT_UNAVAILABLE",
                "AI provider returned an invalid response",
                failure_type=type(exc).__name__,
            ) from None
        finally:
            if stream is not None:
                await stream.close()

    async def complete(self, messages, *, max_tokens=None):
        async with aclosing(self.stream(messages, max_tokens=max_tokens)) as chunks:
            return "".join([part async for part in chunks])
