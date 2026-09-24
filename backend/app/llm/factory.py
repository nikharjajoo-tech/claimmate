"""Chooses the LLM provider(s) for the claim pipeline."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.client import GeminiLLM, StructuredLLM
from app.llm.groq import ChainLLM, GroqLLM


def make_pipeline_llm(settings: Settings | None = None) -> StructuredLLM:
    """auto: Groq first (higher free limits, low latency), Gemini as cross-provider fallback.
    groq / gemini: that provider only, so eval runs measure a single provider."""
    settings = settings or get_settings()
    mode = settings.pipeline_provider
    providers: list[StructuredLLM] = []
    if mode in ("auto", "groq") and settings.groq_api_key:
        providers.append(GroqLLM(settings))
    if mode in ("auto", "gemini") and settings.has_api_key:
        providers.append(GeminiLLM(settings))
    return providers[0] if len(providers) == 1 else ChainLLM(providers)
