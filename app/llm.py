import asyncio
import json
import math
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(Exception):
    pass


class GroqRateLimited(ModelUnavailable):
    def __init__(self, retry_after_seconds: int | None):
        self.retry_after_seconds = retry_after_seconds
        wait = (
            f" Wait at least {retry_after_seconds} seconds before retrying."
            if retry_after_seconds is not None else " Check your Groq account for the quota reset time."
        )
        super().__init__("Groq rate limit reached." + wait + " Avoid repeatedly submitting the request.")


def retry_after_seconds(response: httpx.Response) -> int | None:
    try:
        seconds = float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None
    return max(1, math.ceil(seconds)) if math.isfinite(seconds) and seconds >= 0 else None


class InvalidModelOutput(Exception):
    pass


class UnsupportedDemoRequest(Exception):
    pass


class StructuredLLM(Protocol):
    async def generate(self, schema: type[T], system: str, payload: dict) -> T: ...


class GroqLLM:
    def __init__(self, settings: Settings):
        self.settings = settings

    def completion_budget(self, schema: type[BaseModel], payload: dict) -> int:
        if schema.__name__ == "Plan":
            return self.settings.groq_plan_tokens
        fmt = payload.get("document", {}).get("document_format", {})
        word_limit = fmt.get("max_words_per_section")
        if isinstance(word_limit, int) and 0 < word_limit <= 100:
            return min(self.settings.groq_brief_section_tokens, self.settings.groq_section_tokens)
        return self.settings.groq_section_tokens

    async def generate(self, schema: type[T], system: str, payload: dict) -> T:
        api_key = self.settings.groq_api_key.get_secret_value().strip()
        if not api_key:
            raise ModelUnavailable("GROQ_API_KEY is missing. Add your Groq API key to .env and restart the API.")
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                for attempt in range(3):
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": self.settings.groq_model,
                            "stream": False,
                            "response_format": {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": schema.__name__,
                                    "strict": True,
                                    "schema": schema.model_json_schema(),
                                },
                            },
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
                            ],
                            "temperature": 0.2,
                            "max_completion_tokens": self.completion_budget(schema, payload),
                        },
                    )
                    if response.status_code != 429 or attempt == 2:
                        break
                    # Free-tier token limits can be reached across dependent calls.
                    # Honor numeric Retry-After up to 60s; never wait hours on a daily quota.
                    delay = retry_after_seconds(response)
                    if delay is not None and delay > 60:
                        break
                    await asyncio.sleep(delay if delay is not None else 60)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                detail = "Groq rejected the credentials or model access. Check GROQ_API_KEY and account permissions."
            elif status == 429:
                raise GroqRateLimited(retry_after_seconds(exc.response)) from exc
            elif status in (400, 404, 413, 422):
                detail = (
                    "Groq could not accept the request. Check GROQ_MODEL supports strict structured outputs "
                    "and is available to your account, or shorten the request."
                )
            else:
                detail = "Groq is temporarily unavailable. Please try again later."
            # Never forward provider response bodies, request headers or credentials.
            raise ModelUnavailable(detail) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable("Could not connect to Groq or the request timed out. Check your internet connection and retry.") from exc
        try:
            choice = response.json()["choices"][0]
            if not isinstance(choice, dict):
                raise InvalidModelOutput("Groq returned an invalid response envelope. Please retry the request.")
            if choice.get("finish_reason") != "stop":
                raise InvalidModelOutput("Groq did not finish the structured response. Try a shorter request.")
            return schema.model_validate_json(choice["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise InvalidModelOutput("Groq returned invalid structured output. Please retry the request.") from exc
