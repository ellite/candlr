"""Builds an iCalendar (RFC 5545) feed of a user's cards, no HTTP concerns.

Every date is an all-day event that repeats yearly. A date with a known year
starts on that year, so calendar apps don't show it before it first happened;
one without starts in a fixed leap reference year."""

from datetime import date, datetime, timedelta, timezone

from .models import Person

# Leap, so a Feb 29 date with no year is still a valid DTSTART.
REFERENCE_YEAR = 2000
REFRESH_INTERVAL = "PT12H"


def escape_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    """Folds at 75 octets (not characters), never inside a UTF-8 sequence.
    Continuation lines start with a space, which counts toward their 75."""
    out = []
    current = ""
    size = 0
    limit = 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            out.append(current)
            current, size, limit = " ", 1, 75
        current += char
        size += width
    out.append(current)
    return "\r\n".join(out)


def _stamp(value) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event_lines(person: Person, event, user_id: int) -> list[str]:
    year = event.year if event.year_known and event.year else REFERENCE_YEAR
    start = date(year, event.month, event.day)
    end = start + timedelta(days=1)
    if event.month == 2 and event.day == 29:
        # Candlr shows Feb 29 on Feb 28 in non-leap years, so the feed does
        # the same: the last of the two candidate days that exists that year.
        rule = "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
    else:
        rule = "FREQ=YEARLY"
    summary = f"{person.name}'s {event.event_type.name}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:candlr-{user_id}-event-{event.id}@candlr",
        f"DTSTAMP:{_stamp(event.created_at)}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
        f"DTEND;VALUE=DATE:{end:%Y%m%d}",
        f"RRULE:{rule}",
        f"SUMMARY:{escape_text(summary)}",
    ]
    if event.notes:
        lines.append(f"DESCRIPTION:{escape_text(event.notes)}")
    lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    return lines


def build_calendar(people: list[Person], user_id: int) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Candlr//Birthday Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Candlr",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH_INTERVAL}",
        f"X-PUBLISHED-TTL:{REFRESH_INTERVAL}",
    ]
    for person in sorted(people, key=lambda p: p.name.lower()):
        for event in person.events:
            lines += _event_lines(person, event, user_id)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
