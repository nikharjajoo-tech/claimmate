from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import get_settings
from app.llm.client import CircuitBreaker, GeminiLLM, LLMError


class Answer(BaseModel):
    value: str


class APIError(Exception):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


def ok(value="yes"):
    return SimpleNamespace(
        parsed=Answer(value=value),
        text=f'{{"value": "{value}"}}',
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=3),
    )


class FakeClient:
    """Plays back scripted outcomes per model. An Exception entry is raised; anything else returned."""

    def __init__(self, script: dict[str, list]):
        self.script = {model: list(outcomes) for model, outcomes in script.items()}
        self.calls: list[str] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


SETTINGS = replace(
    get_settings(),
    extract_model="primary",
    fallback_models=("backup", "last"),
    llm_attempts_per_model=2,
    llm_timeout_s=1,
    llm_cooldown_s=60,
)


async def no_sleep(_):
    pass


def make(script, clock=None):
    client = FakeClient(script)
    breaker = CircuitBreaker(SETTINGS.llm_cooldown_s, clock=clock or Clock())
    return GeminiLLM(SETTINGS, client=client, breaker=breaker, sleep=no_sleep), client


async def generate(llm):
    return await llm.generate(step="test", system="s", prompt="p", schema=Answer)


async def test_success_first_try_records_usage():
    llm, client = make({"primary": [ok("hi")]})
    result = await generate(llm)
    assert result.value.value == "hi"
    assert result.call.model == "primary"
    assert (result.call.attempts, result.call.input_tokens, result.call.output_tokens) == (1, 10, 3)
    assert not result.call.fell_back


async def test_retries_transient_error_on_same_model():
    llm, client = make({"primary": [APIError(503), ok()]})
    result = await generate(llm)
    assert client.calls == ["primary", "primary"]
    assert result.call.attempts == 2 and not result.call.fell_back


async def test_falls_back_after_retries_exhausted():
    llm, client = make({"primary": [APIError(503), APIError(500)], "backup": [ok()]})
    result = await generate(llm)
    assert client.calls == ["primary", "primary", "backup"]
    assert result.call.model == "backup" and result.call.fell_back


@pytest.mark.parametrize("code", [404, 429])
async def test_unavailable_or_exhausted_model_is_skipped_without_retry(code):
    llm, client = make({"primary": [APIError(code)], "backup": [ok()]})
    await generate(llm)
    assert client.calls == ["primary", "backup"]


async def test_invalid_json_is_retried():
    bad = SimpleNamespace(parsed=None, text="not json", usage_metadata=None)
    llm, client = make({"primary": [bad, ok()]})
    assert (await generate(llm)).call.attempts == 2


async def test_non_retryable_error_raises_immediately():
    llm, client = make({"primary": [APIError(400)]})
    with pytest.raises(LLMError, match="test failed on primary"):
        await generate(llm)
    assert client.calls == ["primary"]


async def test_all_models_failing_raises():
    llm, client = make({m: [APIError(503), APIError(503)] for m in ("primary", "backup", "last")})
    with pytest.raises(LLMError, match="all models"):
        await generate(llm)
    assert len(client.calls) == 6


async def test_open_circuit_skips_failed_model_on_next_step():
    clock = Clock()
    llm, client = make({"primary": [APIError(503), APIError(503), ok("recovered")], "backup": [ok(), ok()]}, clock)
    await generate(llm)  # primary fails twice -> circuit opens -> backup answers
    client.calls.clear()

    await generate(llm)  # within cooldown: go straight to backup
    assert client.calls == ["backup"]

    clock.now += 61  # cooldown over: primary is preferred again
    client.calls.clear()
    assert (await generate(llm)).value.value == "recovered"
    assert client.calls == ["primary"]


def test_breaker_orders_open_models_last_but_keeps_them():
    clock = Clock()
    breaker = CircuitBreaker(60, clock=clock)
    breaker.trip("a")
    assert breaker.order(["a", "b"]) == ["b", "a"]
    clock.now += 61
    assert breaker.order(["a", "b"]) == ["a", "b"]
