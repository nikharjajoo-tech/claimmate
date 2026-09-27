"""Runtime settings from the environment. Model IDs live here, never in code paths."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "backend" / "data"


@dataclass(frozen=True)
class Settings:
    google_api_key: str
    groq_api_key: str
    groq_models: tuple[str, ...]
    groq_vision_models: tuple[str, ...]
    pipeline_provider: str  # "auto" | "groq" | "gemini"
    pipeline_mode: str  # "split" (extract + classify) | "single" (one call)
    prompt_version: str  # promoted only after it beats the current version in the eval
    database_url: str
    turso_auth_token: str
    evidence_dir: Path
    adjuster_passcode: str  # empty disables the adjuster API
    session_secret: str  # signs adjuster sessions; random per process if unset
    extract_model: str
    fallback_models: tuple[str, ...]
    live_model: str
    llm_attempts_per_model: int
    llm_timeout_s: float
    llm_cooldown_s: float
    llm_max_rate_limit_wait_s: float  # live calls should not wait long; batch evals can

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
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_models=tuple(
            m.strip()
            for m in os.getenv("CLAIMVOICE_GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b").split(",")
            if m.strip()
        ),
        groq_vision_models=tuple(
            m.strip() for m in os.getenv("CLAIMVOICE_GROQ_VISION_MODELS", "qwen/qwen3.8-27b").split(",") if m.strip()
        ),
        pipeline_provider=os.getenv("CLAIMVOICE_PIPELINE_PROVIDER", "auto").strip().lower(),
        pipeline_mode=os.getenv("CLAIMVOICE_PIPELINE_MODE", "split").strip().lower(),
        prompt_version=os.getenv("CLAIMVOICE_PROMPT_VERSION", "v1").strip().lower(),
        # CLAIMVOICE_DATABASE_URL may be a SQLAlchemy URL or a Turso libsql:// URL (decision D7).
        # An empty value (e.g. "CLAIMVOICE_DATABASE_URL=" in .env) means the default, not "no URL".
        database_url=os.getenv("CLAIMVOICE_DATABASE_URL", "").strip() or f"sqlite+aiosqlite:///{DATA_DIR / 'claimvoice.db'}",
        turso_auth_token=os.getenv("TURSO_AUTH_TOKEN", "").strip(),
        evidence_dir=Path(os.getenv("CLAIMVOICE_EVIDENCE_DIR", "").strip() or str(DATA_DIR / "evidence")),
        adjuster_passcode=os.getenv("CLAIMVOICE_ADJUSTER_PASSCODE", "").strip(),
        session_secret=os.getenv("CLAIMVOICE_SESSION_SECRET", "").strip() or secrets.token_hex(32),
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
        llm_max_rate_limit_wait_s=float(os.getenv("CLAIMVOICE_LLM_MAX_RATE_LIMIT_WAIT_S", "10")),
    )
