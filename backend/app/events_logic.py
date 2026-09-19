"""Date math shared by the Event model (days_until) and the people router
(sorting by "upcoming"). Kept separate from models.py since it's plain
calendar logic, not persistence."""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings


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


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        # Feb 29 in a non-leap year - treat as Feb 28 rather than erroring.
        return date(year, month, 28)


def next_occurrence(month: int, day: int, today: date | None = None) -> date:
    today = today or _today()
    occurrence = _safe_date(today.year, month, day)
    if occurrence < today:
        occurrence = _safe_date(today.year + 1, month, day)
    return occurrence


def previous_occurrence(month: int, day: int, today: date | None = None) -> date:
    today = today or _today()
    occurrence = _safe_date(today.year, month, day)
    if occurrence > today:
        occurrence = _safe_date(today.year - 1, month, day)
    return occurrence


def days_until(month: int, day: int, today: date | None = None) -> int:
    today = today or _today()
    return (next_occurrence(month, day, today) - today).days


def days_since(month: int, day: int, today: date | None = None) -> int:
    today = today or _today()
    return (today - previous_occurrence(month, day, today)).days
