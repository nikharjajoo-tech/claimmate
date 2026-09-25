import json
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from app.config import get_settings
from app.domain.models import ClaimFacts
from app.llm.client import CircuitBreaker, GeminiLLM, LLMCall, LLMError, LLMResult
from app.llm.factory import make_pipeline_llm
from app.llm.groq import ChainLLM, GroqLLM, strict_schema


class Answer(BaseModel):
    value: str


SETTINGS = replace(
    get_settings(),
    groq_api_key="test-key",
    groq_models=("big", "small"),
    llm_attempts_per_model=2,
    llm_cooldown_s=60,
)


def completion(value="yes", status=200, headers=None, content=None):
    body = {
        "choices": [{"message": {"content": content if content is not None else json.dumps({"value": value})}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7},
    }
    return httpx.Response(status, json=body if status == 200 else {"error": "x"}, headers=headers or {})


def make(script):
    """script: model -> list of httpx.Response, consumed in order."""
    queues = {m: list(r) for m, r in script.items()}
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return queues[body["model"]].pop(0)

    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    llm = GroqLLM(SETTINGS, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=sleep,
                  breaker=CircuitBreaker(60))
    return llm, requests, sleeps


async def generate(llm):
    return await llm.generate(step="test", system="sys", prompt="p", schema=Answer)


def test_strict_schema_requires_every_field_and_forbids_extras():
    schema = strict_schema(ClaimFacts)

    def walk(node):
        if isinstance(node, dict):
            assert not {"default", "minimum", "format"} & node.keys()
            if node.get("type") == "object" and "properties" in node:
                assert node["required"] == list(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert "estimated_loss_usd" in schema["required"]


async def test_success_sends_strict_schema_and_records_usage():
    llm, requests, _ = make({"big": [completion("hi")]})
    result = await generate(llm)
    assert result.value.value == "hi"
    assert (result.call.model, result.call.input_tokens, result.call.output_tokens) == ("groq:big", 50, 7)
    fmt = requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert "reasoning_effort" not in requests[0]  # only gpt-oss models accept it
    assert requests[0]["messages"][1]["content"] == "p"


async def test_gpt_oss_models_get_low_reasoning_effort():
    llm, requests, _ = make({"openai/gpt-oss-120b": [completion()]})
    llm.models = ["openai/gpt-oss-120b"]
    await generate(llm)
    assert requests[0]["reasoning_effort"] == "low"


async def test_image_is_sent_as_data_url_content_part():
    llm, requests, _ = make({"big": [completion()]})
    await llm.generate(step="see", system="s", prompt="what is this?", schema=Answer, image=b"\xff\xd8\xffjpeg")
    assert len(requests[0]["messages"]) == 1  # instructions ride in the user turn with the image
    text, image = requests[0]["messages"][0]["content"]
    assert text == {"type": "text", "text": "s\n\nwhat is this?"}
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")


async def test_short_rate_limit_waits_and_retries_same_model():
    llm, requests, sleeps = make({"big": [completion(status=429, headers={"retry-after": "3"}), completion()]})
    result = await generate(llm)
    assert [r["model"] for r in requests] == ["big", "big"]
    assert sleeps == [3.0]
    assert not result.call.fell_back


async def test_long_rate_limit_skips_to_next_model():
    llm, requests, _ = make({"big": [completion(status=429, headers={"retry-after": "3600"})], "small": [completion()]})
    result = await generate(llm)
    assert [r["model"] for r in requests] == ["big", "small"]
    assert result.call.model == "groq:small" and result.call.fell_back


async def test_server_errors_retry_then_fall_back():
    llm, requests, _ = make({"big": [completion(status=503), completion(status=500)], "small": [completion()]})
    await generate(llm)
    assert [r["model"] for r in requests] == ["big", "big", "small"]


async def test_invalid_output_is_retried():
    llm, requests, _ = make({"big": [completion(content="{not json"), completion()]})
    assert (await generate(llm)).call.attempts == 2


async def test_auth_error_raises_immediately():
    llm, requests, _ = make({"big": [completion(status=401)]})
    with pytest.raises(LLMError, match="HTTP 401"):
        await generate(llm)
    assert len(requests) == 1


async def test_chain_falls_through_to_next_provider():
    class Failing:
        async def generate(self, **_):
            raise LLMError("groq down")

    class Working:
        async def generate(self, *, step, system, prompt, schema, image=None):
            return LLMResult[schema](value=schema(value="ok"), call=LLMCall(step=step, model="w", attempts=1, latency_ms=1))

    result = await ChainLLM([Failing(), Working()]).generate(step="s", system="", prompt="", schema=Answer)
    assert result.value.value == "ok"
    with pytest.raises(LLMError, match="groq down"):
        await ChainLLM([Failing()]).generate(step="s", system="", prompt="", schema=Answer)


def test_factory_selects_providers():
    both = replace(SETTINGS, google_api_key="g", pipeline_provider="auto")
    chain = make_pipeline_llm(both)
    assert isinstance(chain, ChainLLM)
    assert [type(p) for p in chain.providers] == [GroqLLM, GeminiLLM]

    assert isinstance(make_pipeline_llm(replace(both, pipeline_provider="groq")), GroqLLM)
    assert isinstance(make_pipeline_llm(replace(both, pipeline_provider="gemini")), GeminiLLM)
    with pytest.raises(LLMError, match="No LLM provider"):
        make_pipeline_llm(replace(both, google_api_key="", groq_api_key=""))


async def test_invalid_json_generation_is_retried_not_skipped():
    failed = httpx.Response(400, json={"error": {"code": "json_validate_failed", "failed_generation": "{inj"}})
    llm, requests, _ = make({"big": [failed, completion()]})
    result = await generate(llm)
    assert [r["model"] for r in requests] == ["big", "big"]
    assert result.call.model == "groq:big"


async def test_other_400s_skip_the_model():
    llm, requests, _ = make({"big": [httpx.Response(400, json={"error": {"code": "model_decommissioned"}})],
                             "small": [completion()]})
    await generate(llm)
    assert [r["model"] for r in requests] == ["big", "small"]
