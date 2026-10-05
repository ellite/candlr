"""One-way CardDAV sync: pulls an address book into cards, the address book
being the master copy. No HTTP routing concerns here (see routers/carddav.py).

Contacts are matched to cards by their vCard UID, never by name, so a rename
upstream updates the card instead of duplicating it. A hand-made card with the
same name as a contact is adopted the first time that contact is seen, which
keeps an earlier .vcf import from turning into duplicates. Names and dates
follow the address book; notes and the reminder toggle on a date stay the
user's. Photos are not synced."""

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session, joinedload

from .config import settings
from .data_transfer import ImportFormatError, ParsedCard, parse_vcards
from .events_logic import start_year_problem
from .images import delete_image
from . import netguard
from .netguard import UnsafeURLError, check_outbound_url
from .models import CardDavAccount, Event, EventType, Person

log = logging.getLogger(__name__)

SOURCE = "carddav"
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_ERRORS_REPORTED = 20
REQUEST_TIMEOUT = 30.0
_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:carddav"}

# Only the properties Candlr reads, so servers that honor partial retrieval
# don't send photos. Servers that ignore it just send everything.
_REPORT_BODY = """<?xml version="1.0" encoding="utf-8"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag/>
    <c:address-data>
      <c:prop name="VERSION"/><c:prop name="UID"/><c:prop name="FN"/><c:prop name="N"/>
      <c:prop name="BDAY"/><c:prop name="ANNIVERSARY"/><c:prop name="X-ANNIVERSARY"/>
    </c:address-data>
  </d:prop>
</c:addressbook-query>"""


class CardDavError(Exception):
    """Anything that stops a sync; the message is shown to the user."""


@dataclass
class RemoteCard:
    href: str
    vcard: str


@dataclass
class SyncResult:
    cards: int = 0  # contacts with a date, i.e. cards kept in sync
    added: int = 0  # new dates
    updated: int = 0  # changed dates
    removed: int = 0  # cards (or dates) dropped because the contact is gone
    skipped: int = 0  # contacts or dates that couldn't be used
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.skipped += 1
        if len(self.errors) < MAX_ERRORS_REPORTED:
            self.errors.append(message)


def _cipher() -> Fernet:
    key = hashlib.sha256(b"candlr-carddav-v1\0" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_password(password: str) -> str:
    return _cipher().encrypt(password.encode()).decode() if password else ""


def decrypt_password(stored: str) -> str:
    if not stored:
        return ""
    try:
        return _cipher().decrypt(stored.encode()).decode()
    except InvalidToken:
        raise CardDavError("The saved password can't be read, probably because SECRET_KEY changed. Enter it again.")


def check_url(url: str) -> None:
    """Rejects anything Candlr may not connect to (see netguard.py)."""
    try:
        check_outbound_url(url)
    except UnsafeURLError as e:
        raise CardDavError(str(e)) from None


def fetch_cards(url: str, username: str, password: str, transport: httpx.BaseTransport | None = None) -> list[RemoteCard]:
    """Asks the address book collection at `url` for every contact (a CardDAV
    addressbook-query REPORT). Redirects are not followed, and the connection
    goes to an address that passed the outbound guard (see netguard.py)."""
    auth = httpx.BasicAuth(username, password) if username else None
    headers = {"Depth": "1", "Content-Type": "application/xml; charset=utf-8", "User-Agent": "Candlr"}
    try:
        with netguard.stream(
            "REPORT", url, content=_REPORT_BODY, headers=headers, auth=auth, timeout=REQUEST_TIMEOUT, transport=transport
        ) as resp:
            if resp.is_redirect:
                where = resp.headers.get("location", "another address")
                raise CardDavError(f"The server redirected to {where}. Use that address instead")
            if resp.status_code in (401, 403):
                raise CardDavError("The server refused the username or password")
            if resp.status_code == 404:
                raise CardDavError("No address book found at that URL")
            if resp.status_code not in (200, 207):
                raise CardDavError(f"The server answered with HTTP {resp.status_code}")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise CardDavError("The address book is too large to sync (25 MB max)")
    except UnsafeURLError as e:
        raise CardDavError(str(e)) from None
    except httpx.HTTPError as e:
        raise CardDavError(f"Could not reach the address book ({type(e).__name__})") from None
    return _parse_multistatus(bytes(body))


def _parse_multistatus(body: bytes) -> list[RemoteCard]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise CardDavError("The server's answer wasn't a CardDAV response. Is that URL an address book?") from None
    cards = []
    for response in root.iter("{DAV:}response"):
        data = response.find(".//c:address-data", _NS)
        if data is None or not (data.text or "").strip():
            continue
        href = (response.findtext("d:href", default="", namespaces=_NS) or "").strip()
        cards.append(RemoteCard(href=href, vcard=data.text))
    return cards


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def apply_cards(db: Session, user_id: int, remote: list[RemoteCard]) -> SyncResult:
    """Merges the address book's contacts into the user's cards and commits."""
    result = SyncResult()

    usable: dict[str, ParsedCard] = {}
    for item in remote:
        try:
            parsed = parse_vcards(item.vcard)
        except ImportFormatError:
            result.error(f"{item.href or 'A contact'}: not a readable vCard")
            continue
        for card in parsed:
            for message in card.errors:
                result.error(message)
            if card.name and card.events:
                usable[card.uid or item.href] = card
    result.cards = len(usable)

    types = db.query(EventType).filter(EventType.user_id == user_id).order_by(EventType.sort_order).all()
    types_by_name = {t.name.lower(): t for t in types}
    next_order = max((t.sort_order for t in types), default=-1) + 1

    def type_for(name: str) -> EventType:
        nonlocal next_order
        event_type = types_by_name.get(name.lower())
        if not event_type:
            event_type = EventType(user_id=user_id, name=name, is_default=False, sort_order=next_order)
            db.add(event_type)
            db.flush()
            next_order += 1
            types_by_name[name.lower()] = event_type
        return event_type

    people = (
        db.query(Person)
        .options(joinedload(Person.events))
        .filter(Person.user_id == user_id)
        .order_by(Person.id)
        .all()
    )
    synced = {p.source_uid: p for p in people if p.source == SOURCE}
    adoptable: dict[str, Person] = {}
    for person in people:
        if person.source is None:
            adoptable.setdefault(person.name.lower(), person)

    if synced and not usable:
        # An empty answer is far more likely a wrong URL or a hiccup than every
        # contact losing its dates; never wipe the synced cards on that.
        raise CardDavError(
            "The address book has no contacts with a birthday or anniversary, so nothing was changed. "
            "If that's right, disconnect the address book instead"
        )

    seen: set[str] = set()
    for uid, card in usable.items():
        wanted = []
        for event in card.events:
            event_type = type_for(event.type_name)
            problem = start_year_problem(event_type.interval, event_type.unit, event.year is not None)
            if problem:
                result.error(f"{card.name!r} ({event_type.name}): {problem}")
                continue
            wanted.append((event, event_type))
        if not wanted:
            continue
        seen.add(uid)

        person = synced.get(uid)
        if person is None:
            person = adoptable.pop(card.name.lower(), None)
            if person is None:
                person = Person(user_id=user_id, name=card.name)
                db.add(person)
            person.source, person.source_uid = SOURCE, uid
            db.flush()
            synced[uid] = person
        person.name = card.name

        by_key = {e.source_key: e for e in person.events if e.source_key}
        unclaimed = [e for e in person.events if not e.source_key]
        keys = set()
        for event, event_type in wanted:
            keys.add(event.source_key)
            existing = by_key.get(event.source_key)
            if existing is None:
                # A date entered by hand (or imported earlier) that matches.
                existing = next(
                    (e for e in unclaimed if (e.event_type_id, e.month, e.day) == (event_type.id, event.month, event.day)),
                    None,
                )
                if existing is not None:
                    unclaimed.remove(existing)
                    existing.source_key = event.source_key
            if existing is None:
                db.add(
                    Event(
                        person_id=person.id,
                        event_type_id=event_type.id,
                        month=event.month,
                        day=event.day,
                        year=event.year,
                        year_known=event.year is not None,
                        notify=True,
                        source_key=event.source_key,
                    )
                )
                result.added += 1
                continue
            fields = dict(
                event_type_id=event_type.id, month=event.month, day=event.day,
                year=event.year, year_known=event.year is not None,
            )
            if any(getattr(existing, name) != value for name, value in fields.items()):
                for name, value in fields.items():
                    setattr(existing, name, value)
                result.updated += 1
        for stale in [e for e in person.events if e.source_key and e.source_key not in keys]:
            db.delete(stale)
            result.removed += 1

    for uid, person in synced.items():
        if uid in seen:
            continue
        manual = [e for e in person.events if not e.source_key]
        if manual:
            # The contact is gone (or has no dates now) but the card has dates
            # of its own: keep it as a plain card.
            for event in person.events:
                if event.source_key:
                    db.delete(event)
            person.source = person.source_uid = None
        else:
            if person.image_filename:
                delete_image(person.image_filename)
            db.delete(person)
        result.removed += 1

    db.commit()
    return result


def sync_user(db: Session, user_id: int, transport: httpx.BaseTransport | None = None) -> SyncResult:
    """Pulls the user's address book now and records the outcome on the
    account. Raises CardDavError (after recording it) if the sync failed."""
    account = db.get(CardDavAccount, user_id)
    if account is None:
        raise CardDavError("No address book is connected")
    account.last_attempt_at = _now()
    db.commit()
    try:
        remote = fetch_cards(account.url, account.username, decrypt_password(account.password), transport)
        result = apply_cards(db, user_id, remote)
    except CardDavError as e:
        db.rollback()
        _record(db, user_id, error=str(e))
        raise
    except Exception:
        db.rollback()
        log.exception("CardDAV sync failed user=%s", user_id)
        _record(db, user_id, error="Sync failed unexpectedly. Check the server logs")
        raise CardDavError("Sync failed unexpectedly. Check the server logs") from None
    account = db.get(CardDavAccount, user_id)
    account.last_sync_at = _now()
    account.last_error = None
    account.last_result = {
        "cards": result.cards, "added": result.added, "updated": result.updated,
        "removed": result.removed, "skipped": result.skipped, "errors": result.errors,
    }
    db.commit()
    return result


def _record(db: Session, user_id: int, error: str) -> None:
    account = db.get(CardDavAccount, user_id)
    if account is not None:
        account.last_error = error
        db.commit()


def run_due(session_factory, now: datetime | None = None, transport: httpx.BaseTransport | None = None) -> None:
    """Called by the reminder worker every minute: syncs each connected
    address book whose last attempt is older than the configured interval.
    Failures are recorded on the account and retried after the same interval."""
    interval = settings.carddav_sync_interval_minutes
    if interval <= 0:
        return
    now = now or _now()
    with session_factory() as db:
        due = [
            a.user_id
            for a in db.query(CardDavAccount).all()
            if a.last_attempt_at is None or now - a.last_attempt_at >= timedelta(minutes=interval)
        ]
    for user_id in due:
        try:
            with session_factory() as db:
                sync_user(db, user_id, transport)
            log.info("CardDAV synced user=%s", user_id)
        except CardDavError as e:
            log.warning("CardDAV sync failed user=%s error=%s", user_id, e)
        except Exception:
            log.error("CardDAV sync crashed user=%s", user_id)
