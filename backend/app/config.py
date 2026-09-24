"""Runtime settings from the environment. Model IDs live here, never in code paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    google_api_key: str
    extract_model: str
    fallback_models: tuple[str, ...]
    live_model: str
    llm_attempts_per_model: int
    llm_timeout_s: float
    llm_cooldown_s: float

    @property
    def model_chain(self) -> list[str]:
        return list(dict.fromkeys([self.extract_model, *self.fallback_models]))

    @property
    def has_api_key(self) -> bool:
        return bool(self.google_api_key)


@lru_cache
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    return Settings(
        google_api_key=os.getenv("GOOGLE_API_KEY", "").strip(),
        extract_model=os.getenv("CLAIMVOICE_EXTRACT_MODEL", "gemini-3.8-flash"),
        fallback_models=tuple(
            m.strip()
            for m in os.getenv("CLAIMVOICE_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3-flash-preview").split(",")
            if m.strip()
        ),
        live_model=os.getenv("CLAIMVOICE_LIVE_MODEL", "gemini-3.8-live"),
        llm_attempts_per_model=int(os.getenv("CLAIMVOICE_LLM_ATTEMPTS_PER_MODEL", "2")),
        llm_timeout_s=float(os.getenv("CLAIMVOICE_LLM_TIMEOUT_S", "25")),
        llm_cooldown_s=float(os.getenv("CLAIMVOICE_LLM_COOLDOWN_S", "60")),
    )
