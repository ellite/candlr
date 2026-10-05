"""Date math shared by the Event model (days_until), the people router
(sorting by "upcoming") and the reminder worker. Kept separate from models.py
since it's plain calendar logic, not persistence.

An event type has a cadence: "every `interval` `unit`s", where unit is day,
week, month or year (the default, every 1 year, is a plain birthday-style
date). A date's month/day/year is where the cadence starts. Two cadences need
no start year because they only depend on the month and day (every 1 year,
every 1 month); every other one counts from the start date, so it needs a
known year."""

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

UNITS = ("day", "week", "month", "year")
MAX_INTERVAL = 1000
# What days_since reports for a date that hasn't happened yet (a start date in
# the future): far beyond any "last week" window.
NEVER = 100000


def _today() -> date:
    """"Today" in the configured TIMEZONE, not the server's local/system
    time - the two only match by coincidence otherwise, and a mismatch is
    exactly the kind of thing that makes a birthday reminder land a day
    early or late."""
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def needs_start_year(interval: int, unit: str) -> bool:
    return not (interval == 1 and unit in ("month", "year"))


def cadence_label(interval: int, unit: str) -> str:
    return f"every {unit}" if interval == 1 else f"every {interval} {unit}s"


def start_year_problem(interval: int, unit: str, year_known: bool) -> str | None:
    """Why a date can't use this cadence, or None if it can."""
    if needs_start_year(interval, unit) and not year_known:
        return f"A type that repeats {cadence_label(interval, unit)} needs a full start date, including the year"
    return None


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        # Feb 29 in a non-leap year - treat as Feb 28 rather than erroring.
        return date(year, month, 28)


def _monthly_date(year: int, month: int, day: int) -> date:
    """The given day of a month, clamped to the month's last day (the 31st
    becomes the 30th in April, the 28th or 29th in February)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _nth(month: int, day: int, year: int, interval: int, unit: str, k: int) -> date:
    """The k-th occurrence (0 is the start date itself)."""
    if unit in ("day", "week"):
        step = interval * (7 if unit == "week" else 1) * k
        return _safe_date(year, month, day) + timedelta(days=step)
    if unit == "month":
        return _monthly_date(*_shift_month(year, month, interval * k), day)
    return _safe_date(year + interval * k, month, day)


def _first_k_on_or_after(month: int, day: int, year: int, interval: int, unit: str, today: date) -> int:
    start = _safe_date(year, month, day)
    if today <= start:
        return 0
    if unit in ("day", "week"):
        period = interval * (7 if unit == "week" else 1)
        return -((start - today).days // period)  # ceiling division
    if unit == "month":
        k = max(0, ((today.year - year) * 12 + today.month - month) // interval - 1)
    else:
        k = max(0, (today.year - year) // interval - 1)
    while _nth(month, day, year, interval, unit, k) < today:
        k += 1
    return k


def _anchored(year, interval, unit) -> bool:
    return year is not None and needs_start_year(interval, unit)


def next_occurrence(
    month: int, day: int, today: date | None = None, year: int | None = None, interval: int = 1, unit: str = "year"
) -> date:
    today = today or _today()
    if _anchored(year, interval, unit):
        return _nth(month, day, year, interval, unit, _first_k_on_or_after(month, day, year, interval, unit, today))
    if unit == "month":
        occurrence = _monthly_date(today.year, today.month, day)
        if occurrence < today:
            occurrence = _monthly_date(*_shift_month(today.year, today.month, 1), day)
        return occurrence
    occurrence = _safe_date(today.year, month, day)
    if occurrence < today:
        occurrence = _safe_date(today.year + 1, month, day)
    return occurrence


def previous_occurrence(
    month: int, day: int, today: date | None = None, year: int | None = None, interval: int = 1, unit: str = "year"
) -> date | None:
    """The latest occurrence on or before today, or None if it hasn't started."""
    today = today or _today()
    if _anchored(year, interval, unit):
        k = _first_k_on_or_after(month, day, year, interval, unit, today)
        if _nth(month, day, year, interval, unit, k) == today:
            return today
        return _nth(month, day, year, interval, unit, k - 1) if k > 0 else None
    if unit == "month":
        occurrence = _monthly_date(today.year, today.month, day)
        if occurrence > today:
            occurrence = _monthly_date(*_shift_month(today.year, today.month, -1), day)
        return occurrence
    occurrence = _safe_date(today.year, month, day)
    if occurrence > today:
        occurrence = _safe_date(today.year - 1, month, day)
    return occurrence


def days_until(
    month: int, day: int, today: date | None = None, year: int | None = None, interval: int = 1, unit: str = "year"
) -> int:
    today = today or _today()
    return (next_occurrence(month, day, today, year, interval, unit) - today).days


def days_since(
    month: int, day: int, today: date | None = None, year: int | None = None, interval: int = 1, unit: str = "year"
) -> int:
    today = today or _today()
    previous = previous_occurrence(month, day, today, year, interval, unit)
    return NEVER if previous is None else (today - previous).days


def occurs_on(
    month: int, day: int, today: date, year: int | None = None, interval: int = 1, unit: str = "year"
) -> bool:
    return next_occurrence(month, day, today, year, interval, unit) == today


def elapsed(month: int, day: int, year: int, unit: str, today: date) -> int:
    """Whole `unit`s from the start date to today, for "7 months" style
    labels on a recurring date."""
    if unit in ("day", "week"):
        days = (today - _safe_date(year, month, day)).days
        return days // 7 if unit == "week" else days
    if unit == "month":
        return (today.year - year) * 12 + today.month - month
    return today.year - year
