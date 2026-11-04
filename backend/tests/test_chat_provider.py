import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from app.chat.provider import ChatProviderError, GeminiProvider, retry_seconds
from app.config import Settings


def config(**kwargs):
    return Settings(_env_file=None, gemini_api_key="synthetic-test-key", **kwargs)


def event(content=None, finish=None, **delta):
    return (
        "data: "
        + json.dumps(
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content, **delta},
                        "finish_reason": finish,
                    }
                ],
            }
        )
        + "\n\n"
    )


def response(body):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=body + "data: [DONE]\n\n",
    )


@pytest.mark.asyncio
async def test_stream_uses_google_and_does_not_emit_reasoning():
    def handler(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        data = json.loads(request.content)
        assert data["model"] == "gemini-3.6-flash" and data["stream"]
        assert data["reasoning_effort"] == "low"
        assert "tools" not in data
        return response(
            event(reasoning_content="hidden") + event("18 ") + event("days", "stop")
        )

    provider = GeminiProvider(config(), transport=httpx.MockTransport(handler))
    assert (
        await provider.complete([{"role": "user", "content": "synthetic"}]) == "18 days"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        (event("partial", "length"), "CHAT_INCOMPLETE"),
        (event("partial"), "CHAT_INCOMPLETE"),
        (event(finish="stop"), "CHAT_EMPTY_RESPONSE"),
        (event(finish="content_filter"), "CHAT_INCOMPLETE"),
    ],
)
async def test_non_success_stream_is_never_complete(body, code):
    provider = GeminiProvider(
        config(), transport=httpx.MockTransport(lambda r: response(body))
    )
    with pytest.raises(ChatProviderError) as error:
        await provider.complete([{"role": "user", "content": "synthetic"}])
    assert error.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (429, "CHAT_RATE_LIMITED"),
        (401, "CHAT_CONFIGURATION_ERROR"),
        (403, "CHAT_CONFIGURATION_ERROR"),
        (404, "CHAT_MODEL_UNAVAILABLE"),
        (500, "CHAT_UNAVAILABLE"),
    ],
)
async def test_errors_redacted_and_no_automatic_retry(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"retry-after": "7"},
            json={"error": {"message": "SECRET synthetic-provider-body"}},
        )

    provider = GeminiProvider(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(ChatProviderError) as error:
        await provider.complete([{"role": "user", "content": "synthetic"}])
    assert error.value.code == code
    assert "SECRET" not in str(error.value)
    assert len(requests) == 1
    if status == 429:
        assert error.value.retry_after == 7


@pytest.mark.asyncio
async def test_missing_key_and_input_limit_never_call_network():
    provider = GeminiProvider(Settings(_env_file=None, gemini_api_key=""))
    with pytest.raises(ChatProviderError, match="not configured"):
        await provider.complete([{"role": "user", "content": "synthetic"}])
    provider = GeminiProvider(config())
    with pytest.raises(ChatProviderError) as error:
        await provider.complete([{"role": "user", "content": "x" * 48001}])
    assert error.value.code == "CHAT_INPUT_INVALID"


class SlowStream(httpx.AsyncByteStream):
    def __init__(self, initial=False):
        self.closed = False
        self.initial = initial

    async def __aiter__(self):
        if self.initial:
            yield event("partial").encode()
        await asyncio.sleep(10)
        yield event("done", "stop").encode()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_first_and_overall_deadlines_close_stream(initial):
    stream = SlowStream(initial)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )
    )
    provider = GeminiProvider(
        config(chat_first_token_timeout_seconds=0.5, chat_timeout_seconds=1.0),
        transport=transport,
    )
    with pytest.raises(ChatProviderError) as error:
        await provider.complete([{"role": "user", "content": "synthetic"}])
    assert error.value.code == "CHAT_TIMEOUT"
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_is_not_rewritten_as_provider_error():
    stream = SlowStream(True)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )
    )
    provider = GeminiProvider(config(), transport=transport)
    task = asyncio.create_task(
        provider.complete([{"role": "user", "content": "synthetic"}])
    )
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.asyncio
async def test_consumer_stop_closes_transport():
    stream = SlowStream(True)
    provider = GeminiProvider(
        config(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream)),
    )
    async with aclosing(
        provider.stream([{"role": "user", "content": "synthetic"}])
    ) as parts:
        assert await anext(parts) == "partial"
    assert stream.closed


@pytest.mark.asyncio
async def test_output_limit_and_single_terminal_content_chunk():
    provider = GeminiProvider(
        config(),
        transport=httpx.MockTransport(lambda r: response(event("done", "stop"))),
    )
    assert await provider.complete([{"role": "user", "content": "synthetic"}]) == "done"
    provider = GeminiProvider(
        config(chat_max_output_characters=256),
        transport=httpx.MockTransport(lambda r: response(event("x" * 257, "stop"))),
    )
    with pytest.raises(ChatProviderError) as error:
        await provider.complete([{"role": "user", "content": "synthetic"}])
    assert error.value.code == "CHAT_OUTPUT_LIMIT"


def test_retry_after_is_bounded_and_key_not_in_settings_repr():
    assert retry_seconds("7") == 7
    assert retry_seconds("nan") is None
    assert retry_seconds("9000") is None
    assert retry_seconds("invalid") is None
    assert "synthetic-test-key" not in repr(config())
