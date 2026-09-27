"""Deterministic date resolution (prompt v3, decision D10).

The model copies the claimant's words for a date ("Sunday the 20th", "last night", "September 2nd");
this module turns them into a calendar date relative to the call's reference date. The rule when in
doubt: return None. A missing date costs a follow-up question, while a wrong one can send an honest
claim to fraud review.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.domain.models import is_blank

MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
            ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
            ("october", "oct"), ("november", "nov"), ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
WEEKDAYS = {name: i for i, name in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "a": 1, "an": 1}
MAX_LOOKBACK_DAYS = 366

_VAGUE = re.compile(
    r"\b(between|sometime|some time|around|about|roughly|maybe|probably|not sure|unsure|don'?t remember|"
    r"can'?t remember|or so|last week|last month|few days|couple of days|next)\b"
)
_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?"


def _weekday_before(ref: date, weekday: int, *, strictly_before: bool = False) -> date:
    delta = (ref.weekday() - weekday) % 7
    if strictly_before and delta == 0:
        delta = 7
    return ref - timedelta(days=delta)


def _latest_on_or_before(ref: date, month: int | None, day: int) -> date | None:
    """Most recent real date with this day (and month, if given) on or before ref, within a year."""
    for back in range(0, 13):
        year, m = ref.year, ref.month - back
        while m < 1:
            m, year = m + 12, year - 1
        if month is not None and m != month:
            continue
        try:
            candidate = date(year, m, day)
        except ValueError:
            continue  # e.g. the 31st of a 30-day month
        if candidate <= ref:
            return candidate
    return None


def resolve_date(text: str, reference: date) -> date | None:
    """Resolve a claimant's date phrase against the reference date. Returns None if vague,
    contradictory, in the future, or unparseable."""
    if is_blank(text):
        return None
    t = " ".join(text.lower().replace(",", " ").split())

    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if iso:
        try:
            resolved = date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
        return resolved if resolved <= reference else None

    if _VAGUE.search(t):
        return None

    if re.search(r"\b(today|this morning|this afternoon|this evening|tonight|earlier today)\b", t):
        return reference
    if re.search(r"\bday before yesterday\b", t):
        return reference - timedelta(days=2)
    if re.search(r"\b(yesterday|last night)\b", t):
        return reference - timedelta(days=1)
    ago = re.search(r"\b(\d+|one|two|three|four|five|six|seven|a|an)\s+(day|days|week|weeks)\s+ago\b", t)
    if ago:
        n = int(ago[1]) if ago[1].isdigit() else NUMBER_WORDS[ago[1]]
        return reference - timedelta(days=n * (7 if ago[2].startswith("week") else 1))

    weekday = next((WEEKDAYS[w] for w in WEEKDAYS if re.search(rf"\b{w}\b", t)), None)
    month = next((MONTHS[m] for m in MONTHS if re.search(rf"\b{m}\b", t)), None)
    year_match = re.search(r"\b(20\d{2})\b", t)
    year = int(year_match[1]) if year_match else None

    numeric = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)  # US style month/day[/year]
    if numeric:
        month, day = int(numeric[1]), int(numeric[2])
        if numeric[3]:
            year = int(numeric[3]) + (2000 if len(numeric[3]) == 2 else 0)
    else:
        day_text = re.sub(r"\b20\d{2}\b", " ", t)
        day_match = re.search(rf"\b{_ORDINAL}\b", day_text)
        day = int(day_match[1]) if day_match else None

    if day is None:
        if weekday is not None and month is None:
            # "on Monday": the most recent Monday; "last Monday" skips today if today is Monday.
            return _weekday_before(reference, weekday, strictly_before="last" in t)
        return None
    if not 1 <= day <= 31:
        return None

    if year is not None:
        if month is None:
            return None
        try:
            resolved = date(year, month, day)
        except ValueError:
            return None
    else:
        resolved = _latest_on_or_before(reference, month, day)
        if resolved is None:
            return None

    if resolved > reference or (reference - resolved).days > MAX_LOOKBACK_DAYS:
        return None
    if weekday is not None and resolved.weekday() != weekday:
        return None  # "Sunday the 20th" where the 20th is not a Sunday: ask, don't guess
    return resolved


def resolve_iso(text: str, reference: date) -> str:
    resolved = resolve_date(text, reference)
    return resolved.isoformat() if resolved else "not specified"
