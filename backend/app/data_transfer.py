"""Plain import/export logic for a user's cards, no HTTP concerns.

Both formats describe the same thing: cards (people) with one or more dated
events. JSON nests events under each person; CSV is flat, one row per event,
and rows sharing a name (case-insensitive) become one card. Photos are not
part of either format."""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from .models import Event, EventType, Person
from .schemas import EventInput

CSV_COLUMNS = ["name", "type", "month", "day", "year", "notes", "notify"]
MAX_NAME_LENGTH = 100
MAX_TYPE_LENGTH = 64
MAX_ERRORS_REPORTED = 20

_TRUE = {"1", "true", "yes", "y"}
_FALSE = {"0", "false", "no", "n"}
_DATE_RE = re.compile(r"^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})$")


class ImportFormatError(Exception):
    """The file as a whole can't be read (bad JSON, missing columns, ...)."""


@dataclass
class ParsedEvent:
    type_name: Optional[str]
    month: int
    day: int
    year: Optional[int]
    notes: Optional[str]
    notify: bool


@dataclass
class ParsedFile:
    # Keyed by lowercased name so same-named rows land on one card.
    people: dict[str, tuple[str, list[ParsedEvent]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0

    def add(self, name: str, event: ParsedEvent) -> None:
        self.people.setdefault(name.lower(), (name, []))[1].append(event)

    def reject(self, message: str) -> None:
        self.skipped += 1
        if len(self.errors) < MAX_ERRORS_REPORTED:
            self.errors.append(message)


@dataclass
class ImportResult:
    people_created: int = 0
    events_added: int = 0
    events_skipped: int = 0
    event_types_created: int = 0
    invalid_rows: int = 0
    errors: list[str] = field(default_factory=list)


def load_user_people(db: Session, user_id: int) -> list[Person]:
    return (
        db.query(Person)
        .options(joinedload(Person.events).joinedload(Event.event_type))
        .filter(Person.user_id == user_id)
        .order_by(Person.name)
        .all()
    )


def export_json(people: list[Person], event_types: list[EventType]) -> str:
    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_types": [et.name for et in event_types],
        "people": [
            {
                "name": person.name,
                "events": [
                    {
                        "type": event.event_type.name,
                        "month": event.month,
                        "day": event.day,
                        "year": event.year if event.year_known else None,
                        "notes": event.notes,
                        "notify": event.notify,
                    }
                    for event in person.events
                ],
            }
            for person in people
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def export_csv(people: list[Person]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for person in people:
        for event in person.events:
            writer.writerow(
                [
                    person.name,
                    event.event_type.name,
                    event.month,
                    event.day,
                    event.year if event.year_known and event.year else "",
                    event.notes or "",
                    "true" if event.notify else "false",
                ]
            )
    # BOM so Excel opens the file as UTF-8 instead of guessing a code page.
    return "\ufeff" + out.getvalue()


def _validate_date(month, day, year) -> tuple[int, int, Optional[int]]:
    """Reuses EventInput's rules (ranges, Feb 29 only in leap years) so an
    import can never store a date the API itself would reject."""
    try:
        parsed = EventInput(
            event_type_id=0, month=month, day=day, year=year, year_known=year is not None
        )
    except ValidationError as e:
        first = e.errors()[0]
        message = first["msg"].removeprefix("Value error, ")
        field_name = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"{field_name}: {message}" if field_name else message) from None
    return parsed.month, parsed.day, parsed.year


def _parse_bool(value, default: bool = True) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"notify must be true or false, got {value!r}")


def _clean_text(value, limit: int, label: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{label} is longer than {limit} characters")
    return text


def _build_event(type_name, month, day, year, notes, notify) -> ParsedEvent:
    type_name = _clean_text(type_name, MAX_TYPE_LENGTH, "type")
    notes = _clean_text(notes, 10000, "notes")
    try:
        month = int(month)
        day = int(day)
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("month, day and year must be numbers") from None
    month, day, year = _validate_date(month, day, year)
    return ParsedEvent(type_name, month, day, year, notes, _parse_bool(notify))


def _split_date(value: str) -> tuple[str, str, Optional[str]]:
    text = value.strip()
    match = _DATE_RE.match(text[2:] if text.startswith("--") else text)
    if not match:
        raise ValueError(f"unrecognized date {value!r} (use YYYY-MM-DD or MM-DD)")
    year, month, day = match.groups()
    return month, day, year


def parse_csv(text: str) -> ParsedFile:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportFormatError("The CSV file is empty")
    headers = {name.strip().lower(): name for name in reader.fieldnames if name}
    if "name" not in headers:
        raise ImportFormatError("The CSV needs a 'name' column")
    has_date = "date" in headers
    if not has_date and not ("month" in headers and "day" in headers):
        raise ImportFormatError("The CSV needs either 'month' and 'day' columns or a 'date' column")

    def cell(row, key):
        source = headers.get(key)
        return row.get(source) if source else None

    parsed = ParsedFile()
    for line, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values() if isinstance(value, str)):
            continue
        try:
            name = _clean_text(cell(row, "name"), MAX_NAME_LENGTH, "name")
            if not name:
                raise ValueError("name is empty")
            month, day, year = cell(row, "month"), cell(row, "day"), cell(row, "year")
            if not (month or "").strip() and not (day or "").strip() and has_date:
                month, day, year = _split_date(cell(row, "date") or "")
            parsed.add(name, _build_event(cell(row, "type"), month, day, year, cell(row, "notes"), cell(row, "notify")))
        except ValueError as e:
            parsed.reject(f"Row {line}: {e}")
    return parsed


def parse_json(text: str) -> ParsedFile:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ImportFormatError("The file is not valid JSON") from None
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list):
        raise ImportFormatError("The JSON needs a top-level 'people' list")

    parsed = ParsedFile()
    for index, person in enumerate(people, start=1):
        label = f"Person {index}"
        if not isinstance(person, dict):
            parsed.reject(f"{label}: expected an object")
            continue
        try:
            name = _clean_text(person.get("name"), MAX_NAME_LENGTH, "name")
            if not name:
                raise ValueError("name is empty")
        except ValueError as e:
            parsed.reject(f"{label}: {e}")
            continue
        label = f"{name!r}"
        events = person.get("events")
        if not isinstance(events, list) or not events:
            parsed.reject(f"{label}: needs at least one event")
            continue
        for event in events:
            try:
                if not isinstance(event, dict):
                    raise ValueError("expected an object")
                parsed.add(
                    name,
                    _build_event(
                        event.get("type"),
                        event.get("month"),
                        event.get("day"),
                        event.get("year"),
                        event.get("notes"),
                        event.get("notify"),
                    ),
                )
            except ValueError as e:
                parsed.reject(f"{label}: {e}")
    return parsed


def parse_upload(filename: str, raw: bytes) -> ParsedFile:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFormatError("The file must be UTF-8 text") from None
    name = (filename or "").lower()
    if name.endswith(".json") or (not name.endswith(".csv") and text.lstrip().startswith(("{", "["))):
        return parse_json(text)
    return parse_csv(text)


def apply_import(db: Session, user_id: int, parsed: ParsedFile) -> ImportResult:
    """Merges parsed cards into the user's data and commits.

    A card whose name matches an existing one (case-insensitive) gains only
    the dates it doesn't already have, judged by type + month + day, so
    re-importing an export is a no-op. Unknown event types are created."""
    result = ImportResult(invalid_rows=parsed.skipped, errors=list(parsed.errors))

    types = db.query(EventType).filter(EventType.user_id == user_id).order_by(EventType.sort_order).all()
    types_by_name = {et.name.lower(): et for et in types}
    next_order = max((et.sort_order for et in types), default=-1) + 1
    default_type = types[0] if types else None

    existing = {}
    for person in load_user_people(db, user_id):
        existing.setdefault(person.name.lower(), person)

    for key, (name, events) in parsed.people.items():
        person = existing.get(key)
        seen = {(e.event_type_id, e.month, e.day) for e in person.events} if person else set()
        created = False
        for event in events:
            if event.type_name:
                event_type = types_by_name.get(event.type_name.lower())
                if not event_type:
                    event_type = EventType(
                        user_id=user_id, name=event.type_name, is_default=False, sort_order=next_order
                    )
                    db.add(event_type)
                    db.flush()
                    next_order += 1
                    types_by_name[event.type_name.lower()] = event_type
                    result.event_types_created += 1
            elif default_type:
                event_type = default_type
            else:
                result.events_skipped += 1
                continue

            slot = (event_type.id, event.month, event.day)
            if slot in seen:
                result.events_skipped += 1
                continue
            seen.add(slot)

            if not person:
                person = Person(user_id=user_id, name=name)
                db.add(person)
                db.flush()
                existing[key] = person
                created = True
            db.add(
                Event(
                    person_id=person.id,
                    event_type_id=event_type.id,
                    month=event.month,
                    day=event.day,
                    year=event.year,
                    year_known=event.year is not None,
                    notes=event.notes,
                    notify=event.notify,
                )
            )
            result.events_added += 1
        if created:
            result.people_created += 1

    db.commit()
    return result
