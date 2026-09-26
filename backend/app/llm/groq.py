"""Groq structured-output client (OpenAI-compatible chat completions, strict JSON schema)."""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm.client import CircuitBreaker, LLMCall, LLMError, LLMResult

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
RETRYABLE_CODES = {500, 502, 503, 504}
SKIP_MODEL_CODES = {400, 404}  # model not served, or model rejects this schema

# Schema keywords strict mode does not accept; validation still happens client-side with Pydantic.
_UNSUPPORTED_KEYWORDS = {"default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "format"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema rewritten for strict mode: every property required, no extras."""

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {k: visit(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return visit(model.model_json_schema())


class GroqLLM:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        models: list[str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set. Add it to the .env file at the repo root.")
        self._http = http or httpx.AsyncClient(timeout=self.settings.llm_timeout_s)
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker(self.settings.llm_cooldown_s)
        self.models = list(models or self.settings.groq_models)

    async def generate(
        self, *, step: str, system: str, prompt: str, schema: type[T], image: bytes | None = None
    ) -> LLMResult[T]:
        if image is None:
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        else:
            # With an image, instructions go in the user turn: in a small live test (2 samples each)
            # qwen3.8-27b failed strict JSON generation with a system message and succeeded without.
            data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
            messages = [{"role": "user", "content": [
                {"type": "text", "text": f"{system}\n\n{prompt}"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}]
        body = {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)},
            },
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        started = time.monotonic()
        attempts = 0
        last_error = ""

        for model in self.breaker.order(self.models):
            attempt = 0
            while attempt < self.settings.llm_attempts_per_model:
                attempt += 1
                attempts += 1
                request = {**body, "model": model}
                if "gpt-oss" in model:
                    # gpt-oss reasons before answering; reasoning tokens count against rate limits.
                    request["reasoning_effort"] = "low"
                try:
                    response = await self._http.post(GROQ_URL, json=request, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("%s: %s on %s (attempt %d)", step, type(exc).__name__, model, attempt)
                else:
                    code = response.status_code
                    if code == 200:
                        data = response.json()
                        try:
                            value = schema.model_validate_json(data["choices"][0]["message"]["content"])
                        except (ValidationError, KeyError, IndexError, TypeError) as exc:
                            last_error = f"invalid output: {exc}"
                            logger.warning("%s: invalid output from %s (attempt %d)", step, model, attempt)
                        else:
                            self.breaker.reset(model)
                            usage = data.get("usage") or {}
                            return LLMResult[schema](
                                value=value,
                                call=LLMCall(
                                    step=step,
                                    model=f"groq:{model}",
                                    attempts=attempts,
                                    latency_ms=int((time.monotonic() - started) * 1000),
                                    input_tokens=usage.get("prompt_tokens", 0),
                                    output_tokens=usage.get("completion_tokens", 0),
                                    fell_back=model != self.models[0],
                                ),
                            )
                    else:
                        last_error = f"HTTP {code}: {response.text[:300]}"
                        if code == 429:
                            wait = float(response.headers.get("retry-after", "60") or 60)
                            # Short waits are per-minute limits worth sitting out; long ones are daily limits.
                            if wait <= self.settings.llm_max_rate_limit_wait_s:
                                logger.warning("%s: rate limited on %s, waiting %.1fs", step, model, wait)
                                await self._sleep(wait)
                                attempt -= 1  # a short rate-limit wait is not a failed attempt
                                continue
                            logger.warning("%s: %s quota exhausted (retry-after %.0fs)", step, model, wait)
                            break
                        if code == 400 and "json_validate_failed" in response.text:
                            # The model produced invalid JSON mid-generation: a retry can succeed.
                            logger.warning("%s: %s generated invalid JSON (attempt %d)", step, model, attempt)
                        elif code in SKIP_MODEL_CODES:
                            logger.warning("%s: HTTP %s on %s, skipping model", step, code, model)
                            break
                        elif code not in RETRYABLE_CODES:
                            raise LLMError(f"{step} failed on groq:{model}: {last_error}")
                        else:
                            logger.warning("%s: HTTP %s on %s (attempt %d)", step, code, model, attempt)
                if attempt < self.settings.llm_attempts_per_model:
                    await self._sleep(min(4.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25))
            self.breaker.trip(model)

        raise LLMError(f"{step} failed on all Groq models {self.models}: {last_error}")


class ChainLLM:
    """Tries providers in order; the next provider runs only if the previous one fails outright."""

    def __init__(self, providers: list[Any]) -> None:
        if not providers:
            raise LLMError("No LLM provider is configured. Set GROQ_API_KEY or GOOGLE_API_KEY in .env.")
        self.providers = providers

    async def generate(
        self, *, step: str, system: str, prompt: str, schema: type[T], image: bytes | None = None
    ) -> LLMResult[T]:
        errors = []
        for provider in self.providers:
            try:
                return await provider.generate(step=step, system=system, prompt=prompt, schema=schema, image=image)
            except LLMError as exc:
                errors.append(str(exc))
                logger.warning("%s: provider %s failed, trying next", step, type(provider).__name__)
        raise LLMError(" | ".join(errors))
