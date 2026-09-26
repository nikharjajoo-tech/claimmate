"""Structured JSON logs with claim context and PII redaction (PRD FR-9.1, FR-9.3).

Every log line is one JSON object. Phone numbers and email addresses are masked before a line is
written, whether they appear in the message, its arguments, or an exception.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime

claim_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("claim_id", default=None)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 10+ digit phone numbers with common separators: 720-555-0148, (720) 555 0148, +1 720.555.0148
_PHONE = re.compile(r"(?<![\w-])(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])")


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        claim_id = getattr(record, "claim_id", None) or claim_id_var.get()
        if claim_id:
            entry["claim_id"] = claim_id
        for key in ("event", "duration_ms", "route", "model", "status"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info:
            entry["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Route all app and server logs through the JSON formatter. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # Third-party request logs can include URLs with keys in query strings; keep them quiet.
    for name in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(name).setLevel(logging.WARNING)
