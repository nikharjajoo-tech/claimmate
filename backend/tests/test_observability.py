import json
import logging

import pytest

from app.observability import JsonFormatter, claim_id_var, redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("call me at 720-555-0148", "call me at [phone]"),
        ("(720) 555 0148 or +1 720.555.0148", "[phone] or [phone]"),
        ("email marcus.webb@example.com please", "email [email] please"),
        ("policy HO-20417, claim 1ff0167b3b0a43bc", "policy HO-20417, claim 1ff0167b3b0a43bc"),
        ("report DPD-26-4471 on 2026-09-21", "report DPD-26-4471 on 2026-09-21"),
        ("loss was $18,000", "loss was $18,000"),
    ],
)
def test_redact_masks_contacts_but_keeps_claim_data(text, expected):
    assert redact(text) == expected


def make_record(msg, *args, exc=None, **extra):
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, msg, args, exc)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_lines_are_redacted_and_carry_context():
    token = claim_id_var.set("abc123")
    try:
        line = JsonFormatter().format(make_record("claimant said %s", "reach me at jo@example.com", event="turn", duration_ms=42))
    finally:
        claim_id_var.reset(token)
    entry = json.loads(line)
    assert entry["msg"] == "claimant said reach me at [email]"
    assert entry["claim_id"] == "abc123" and entry["event"] == "turn" and entry["duration_ms"] == 42
    assert entry["level"] == "warning" and entry["logger"] == "app.test"


def test_exceptions_are_redacted():
    try:
        raise ValueError("bad contact 720-555-0148")
    except ValueError:
        import sys

        entry = json.loads(JsonFormatter().format(make_record("failed", exc=sys.exc_info())))
    assert "720-555-0148" not in entry["error"] and "[phone]" in entry["error"]
