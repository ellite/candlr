from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..carddav import SOURCE, CardDavError, _now, check_url, encrypt_password, sync_user
from ..database import get_db
from ..dependencies import get_current_user
from ..images import delete_image
from ..models import CardDavAccount, Person, User
from ..schemas import CardDavOut, CardDavSave

router = APIRouter(prefix="/carddav", tags=["carddav"])

# Seconds between manual syncs, so the button can't be used to hammer the
# address book server.
SYNC_COOLDOWN = 10


def _out(account: CardDavAccount) -> CardDavOut:
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value else None

    return CardDavOut(
        url=account.url,
        username=account.username,
        has_password=bool(account.password),
        last_attempt_at=utc(account.last_attempt_at),
        last_sync_at=utc(account.last_sync_at),
        last_error=account.last_error,
        last_result=account.last_result,
    )


@router.get("", response_model=CardDavOut | None)
def get_carddav(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.get(CardDavAccount, current_user.id)
    return _out(account) if account else None


@router.put("", response_model=CardDavOut)
def save_carddav(data: CardDavSave, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    url = data.url.strip()
    try:
        check_url(url)
    except CardDavError as e:
        raise HTTPException(status_code=400, detail=str(e))
    account = db.get(CardDavAccount, current_user.id)
    if account is None:
        account = CardDavAccount(user_id=current_user.id)
        db.add(account)
    elif account.url != url or account.username != data.username.strip():
        account.last_error = None
    account.url = url
    account.username = data.username.strip()
    if data.password is not None:
        account.password = encrypt_password(data.password)
    db.commit()
    return _out(account)


@router.post("/sync", response_model=CardDavOut)
def sync_carddav(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.get(CardDavAccount, current_user.id)
    if account is None:
        raise HTTPException(status_code=400, detail="No address book is connected")
    if account.last_attempt_at and (_now() - account.last_attempt_at).total_seconds() < SYNC_COOLDOWN:
        raise HTTPException(status_code=429, detail="Synced a moment ago. Try again in a few seconds")
    try:
        sync_user(db, current_user.id)
    except CardDavError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _out(db.get(CardDavAccount, current_user.id, populate_existing=True))


@router.delete("")
def disconnect_carddav(
    delete_cards: bool = False, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Disconnects the address book. The synced cards stay as ordinary cards
    unless delete_cards is set."""
    account = db.get(CardDavAccount, current_user.id)
    if account is not None:
        db.delete(account)
    people = db.query(Person).filter(Person.user_id == current_user.id, Person.source == SOURCE).all()
    for person in people:
        if delete_cards:
            if person.image_filename:
                delete_image(person.image_filename)
            db.delete(person)
        else:
            person.source = person.source_uid = None
            for event in person.events:
                event.source_key = None
    db.commit()
    return {"ok": True, "cards": len(people)}
