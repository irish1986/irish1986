import random
from datetime import date

import pytest

from history.main import build_schedule

START = date(2026, 1, 1)
END = date(2026, 3, 31)


def test_within_range_and_hours():
    schedule = build_schedule(START, END, random.Random(1), hours=(9, 18))
    assert schedule
    assert all(START <= s.date() <= END for s in schedule)
    assert all(9 <= s.hour < 18 for s in schedule)
    assert schedule == sorted(schedule)


def test_max_per_day():
    schedule = build_schedule(START, END, random.Random(2), max_per_day=2, skip_chance=0)
    per_day: dict[date, int] = {}
    for s in schedule:
        per_day[s.date()] = per_day.get(s.date(), 0) + 1
    assert max(per_day.values()) <= 2


def test_weekend_factor_zero_means_no_weekend_commits():
    schedule = build_schedule(START, END, random.Random(3), weekend_factor=0)
    assert all(s.weekday() < 5 for s in schedule)


def test_seed_is_deterministic():
    a = build_schedule(START, END, random.Random(42))
    b = build_schedule(START, END, random.Random(42))
    assert a == b


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        build_schedule(END, START, random.Random())
