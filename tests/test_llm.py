import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import GroqLLM, GroqRateLimited, InvalidModelOutput, ModelUnavailable
from app.schemas import Plan, Section


def test_groq_adapter_sends_auth_schema_and_validates_response(load_example, monkeypatch):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop", "message":{"content": json.dumps(load_example()["plan"])}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    plan = asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {"request":"test"}))
    assert plan.title == load_example()["plan"]["title"]
    assert captured[0]["stream"] is False
    assert captured[0]["model"] == "qwen/qwen3.8-27b"
    assert captured[0]["response_format"]["json_schema"] == {
        "name": "Plan", "strict": True, "schema": Plan.model_json_schema(),
    }


@pytest.mark.parametrize("response", [
    {"choices":[{"finish_reason":"stop", "message":{"content":"not json"}}]},
    {"choices":[]},
    {"choices":[None]},
    {"choices":[{"finish_reason":"length", "message":{"content":"{}"}}]},
])
def test_groq_malformed_or_incomplete_response_is_a_model_error(monkeypatch, response):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(InvalidModelOutput):
        asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {"request":"test"}))


@pytest.mark.parametrize("status,expected", [(401,"credentials"), (403,"credentials"), (400,"GROQ_MODEL"), (429,"rate limit"), (500,"unavailable")])
def test_groq_provider_errors_are_safe_and_actionable(monkeypatch, status, expected):
    original_client = httpx.AsyncClient
    # A daily quota should fail promptly instead of sleeping until tomorrow.
    transport = httpx.MockTransport(lambda request: httpx.Response(
        status, json={"error":"sensitive-provider-detail"}, headers={"retry-after":"3600"},
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(ModelUnavailable) as error:
        asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {}))
    assert expected in str(error.value)
    assert "test-key" not in str(error.value)
    assert "sensitive-provider-detail" not in str(error.value)


def test_groq_rate_limit_retry_preserves_success(load_example, monkeypatch):
    calls, delays = [], []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after":"2"})
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(load_example()["plan"])}}]})

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("app.llm.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    result = asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {}))
    assert result.title == load_example()["plan"]["title"]
    assert len(calls) == 2
    assert delays == [2.0]


def test_groq_rate_limit_retries_are_bounded(monkeypatch):
    calls, delays = [], []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        return httpx.Response(429)

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("app.llm.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(ModelUnavailable, match="rate limit"):
        asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {}))
    assert len(calls) == 3
    assert delays == [60.0, 60.0]


@pytest.mark.parametrize("word_limit,expected", [(39,1024), (None,2048), (500,2048)])
def test_section_output_budget_is_smaller_for_brief_requests(monkeypatch, word_limit, expected):
    captured = []
    original_client = httpx.AsyncClient
    result = {"heading":"Updates", "paragraphs":["Share progress."], "bullets":[], "tables":[]}

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(result)}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    settings = Settings(_env_file=None, groq_api_key="test-key", groq_section_tokens=2048, groq_brief_section_tokens=1024)
    asyncio.run(GroqLLM(settings).generate(
        Section, "Return JSON", {"document":{"document_format":{"max_words_per_section":word_limit}}},
    ))
    assert captured[0]["max_completion_tokens"] == expected


def test_long_rate_limit_wait_is_exposed_without_retrying(monkeypatch):
    original_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after":"120.5"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(GroqRateLimited) as error:
        asyncio.run(GroqLLM(Settings(groq_api_key="test-key")).generate(Plan, "Return JSON", {}))
    assert error.value.retry_after_seconds == 121
    assert len(calls) == 1
