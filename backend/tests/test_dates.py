from datetime import date

import pytest

from app.pipeline.dates import resolve_date, resolve_iso

REF = date(2026, 9, 24)  # a Thursday, the eval scenarios' reference date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # relative days
        ("today", "2026-09-24"),
        ("this morning", "2026-09-24"),
        ("yesterday", "2026-09-23"),
        ("last night", "2026-09-23"),
        ("the day before yesterday", "2026-09-22"),
        ("two days ago", "2026-09-22"),
        ("a week ago", "2026-09-17"),
        # weekdays
        ("Monday", "2026-09-21"),
        ("on Tuesday", "2026-09-22"),
        ("Thursday", "2026-09-24"),          # today is Thursday
        ("last Thursday", "2026-09-17"),     # "last" skips today
        # weekday + day of month (the run D failures)
        ("Sunday the 20th", "2026-09-20"),
        ("Monday the 21st", "2026-09-21"),
        ("Tuesday, the 22nd", "2026-09-22"),
        ("Saturday the 19th", "2026-09-19"),
        # month + day, with and without a year
        ("September 2nd", "2026-09-02"),
        ("Sept 12", "2026-09-12"),
        ("May 10th", "2026-05-10"),
        ("July 1st", "2026-07-01"),
        ("December 30th", "2025-12-30"),     # later this year is in the future: last year
        ("September 21st, 2026", "2026-09-21"),
        ("March 18, 2026", "2026-03-18"),
        ("the 14th", "2026-09-14"),
        ("the 28th", "2026-08-28"),          # the 28th of this month hasn't happened yet
        ("9/20", "2026-09-20"),
        ("9/20/2026", "2026-09-20"),
        ("2026-09-15", "2026-09-15"),
    ],
)
def test_resolves_exact_dates(text, expected):
    assert resolve_iso(text, REF) == expected


@pytest.mark.parametrize(
    "text",
    [
        "not specified",
        "",
        "sometime between the 10th and the 15th",   # range
        "last week",                                 # vague
        "I don't remember the exact date",
        "maybe the 15th",
        "Sunday the 21st",                           # the 21st is a Monday: contradiction, ask instead
        "next Monday",                               # future
        "2026-12-25",                                # future ISO date
        "February 30th",                             # not a real date
        "the 45th",
        "a while back",
    ],
)
def test_vague_contradictory_or_future_dates_stay_blank(text):
    assert resolve_date(text, REF) is None


def test_year_boundary():
    jan2 = date(2027, 1, 2)
    assert resolve_iso("December 31st", jan2) == "2026-12-31"
    assert resolve_iso("the 30th", jan2) == "2026-12-30"
    assert resolve_iso("Thursday the 31st", jan2) == "2026-12-31"  # Dec 31, 2026 is a Thursday


def test_skips_months_without_that_day():
    assert resolve_iso("the 31st", date(2026, 10, 15)) == "2026-08-31"  # September has no 31st
