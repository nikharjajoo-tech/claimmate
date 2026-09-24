"""Structured-output LLM client with retry, a model fallback chain, a circuit breaker,
and usage accounting."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

RETRYABLE_CODES = {500, 502, 503, 504}
# Skip to the next model without retrying: 404 = model not served for this key,
# 429 = quota exhausted (free tier is per model per day, so a quick retry cannot succeed).
SKIP_MODEL_CODES = {404, 429}


class LLMCall(BaseModel):
    """One logical LLM step, possibly spanning several attempts and models."""

    step: str
    model: str
    attempts: int
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    fell_back: bool = False


class LLMResult(BaseModel, Generic[T]):
    value: T
    call: LLMCall


class LLMError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    async def generate(self, *, step: str, system: str, prompt: str, schema: type[T]) -> LLMResult[T]: ...


class CircuitBreaker:
    """Skips a model for a cooldown period after it exhausts its retries.

    Without this, every pipeline step during an outage would re-pay the full retry cost on
    the primary model before reaching a healthy fallback.
    """

    def __init__(self, cooldown_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._open_until: dict[str, float] = {}

    def is_open(self, model: str) -> bool:
        return self._open_until.get(model, 0.0) > self._clock()

    def trip(self, model: str) -> None:
        self._open_until[model] = self._clock() + self.cooldown_s
        logger.warning("circuit open for %s (%.0fs)", model, self.cooldown_s)

    def reset(self, model: str) -> None:
        self._open_until.pop(model, None)

    def order(self, models: list[str]) -> list[str]:
        """Healthy models first, in preference order. Open ones last rather than never,
        so a total outage still gets one more try instead of failing instantly."""
        return [m for m in models if not self.is_open(m)] + [m for m in models if self.is_open(m)]


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


class GeminiLLM:
    """Gemini with JSON-schema output, walking the configured model chain."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        if client is None:
            if not self.settings.has_api_key:
                raise LLMError("GOOGLE_API_KEY is not set. Add it to the .env file at the repo root.")
            from google import genai

            client = genai.Client(api_key=self.settings.google_api_key)
        self._client = client
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker(self.settings.llm_cooldown_s)
        self.models = self.settings.model_chain

    async def generate(self, *, step: str, system: str, prompt: str, schema: type[T]) -> LLMResult[T]:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        started = time.monotonic()
        attempts = 0
        last_error: Exception | None = None

        for model in self.breaker.order(self.models):
            for attempt in range(self.settings.llm_attempts_per_model):
                attempts += 1
                try:
                    response = await asyncio.wait_for(
                        self._client.aio.models.generate_content(model=model, contents=prompt, config=config),
                        timeout=self.settings.llm_timeout_s,
                    )
                    value = response.parsed if isinstance(response.parsed, schema) else schema.model_validate_json(response.text)
                except (TimeoutError, ValidationError) as exc:
                    last_error = exc
                    logger.warning("%s: %s on %s (attempt %d)", step, type(exc).__name__, model, attempt + 1)
                except Exception as exc:
                    code = _error_code(exc)
                    last_error = exc
                    if code in SKIP_MODEL_CODES:
                        logger.warning("%s: HTTP %s on %s, skipping to next model", step, code, model)
                        break
                    if code not in RETRYABLE_CODES:
                        raise LLMError(f"{step} failed on {model}: {exc}") from exc
                    logger.warning("%s: HTTP %s on %s (attempt %d)", step, code, model, attempt + 1)
                else:
                    self.breaker.reset(model)
                    usage = response.usage_metadata
                    return LLMResult[schema](
                        value=value,
                        call=LLMCall(
                            step=step,
                            model=model,
                            attempts=attempts,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
                            fell_back=model != self.models[0],
                        ),
                    )
                if attempt + 1 < self.settings.llm_attempts_per_model:
                    await self._sleep(min(4.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))
            self.breaker.trip(model)

        raise LLMError(f"{step} failed on all models {self.models}: {type(last_error).__name__}: {last_error}")
