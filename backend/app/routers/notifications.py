from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..dependencies import get_current_user
from ..models import NotificationChannel, PushSubscription, User
from ..notifiers import dispatch
from ..notifiers.errors import NotifierError
from ..schemas import (
    NotificationChannelOut,
    NotificationChannelUpdate,
    NotifyPreferencesOut,
    NotifyPreferencesUpdate,
    PushSubscriptionCreate,
    PushSubscriptionDelete,
    VapidPublicKeyOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# webpush is intentionally excluded from manual enable/config - it's managed
# by the subscribe/unsubscribe endpoints instead, since its "config" is a set
# of per-device subscriptions rather than a single form.
CONFIGURABLE_CHANNELS = ["email", "ntfy", "discord", "telegram", "pushover"]
ALL_CHANNELS = CONFIGURABLE_CHANNELS + ["webpush"]


def _validate_config(channel: str, config: dict) -> None:
    if channel == "ntfy" and not (config.get("topic") or "").strip():
        raise HTTPException(status_code=400, detail="ntfy topic is required")
    if channel == "discord" and not (config.get("webhook_url") or "").strip():
        raise HTTPException(status_code=400, detail="Discord webhook URL is required")
    if channel == "telegram" and not (
        (config.get("bot_token") or "").strip() and (config.get("chat_id") or "").strip()
    ):
        raise HTTPException(status_code=400, detail="Telegram bot token and chat ID are required")
    if channel == "pushover" and not (
        (config.get("user_key") or "").strip() and (config.get("api_token") or "").strip()
    ):
        raise HTTPException(status_code=400, detail="Pushover user key and API token are required")


def _get_channel_row(db: Session, user_id: int, channel: str) -> NotificationChannel | None:
    return (
        db.query(NotificationChannel)
        .filter(NotificationChannel.user_id == user_id, NotificationChannel.channel == channel)
        .first()
    )


@router.get("/preferences", response_model=NotifyPreferencesOut)
def get_notify_preferences(current_user: User = Depends(get_current_user)):
    return NotifyPreferencesOut(notify_time=current_user.notify_time)


@router.put("/preferences", response_model=NotifyPreferencesOut)
def update_notify_preferences(
    data: NotifyPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.notify_time = data.notify_time
    db.commit()
    return NotifyPreferencesOut(notify_time=current_user.notify_time)


@router.get("/channels", response_model=list[NotificationChannelOut])
def list_channels(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    saved = {c.channel: c for c in db.query(NotificationChannel).filter(NotificationChannel.user_id == current_user.id)}
    return [
        NotificationChannelOut(
            channel=ch,
            enabled=saved[ch].enabled if ch in saved else False,
            config=saved[ch].config if ch in saved else {},
        )
        for ch in ALL_CHANNELS
    ]


@router.put("/channels/{channel}", response_model=NotificationChannelOut)
def update_channel(
    channel: str,
    data: NotificationChannelUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if channel not in CONFIGURABLE_CHANNELS:
        raise HTTPException(status_code=404, detail="Unknown channel")
    if data.enabled:
        _validate_config(channel, data.config)

    row = _get_channel_row(db, current_user.id, channel)
    if row:
        row.enabled = data.enabled
        row.config = data.config
    else:
        row = NotificationChannel(user_id=current_user.id, channel=channel, enabled=data.enabled, config=data.config)
        db.add(row)
    db.commit()
    db.refresh(row)
    return NotificationChannelOut(channel=row.channel, enabled=row.enabled, config=row.config)


@router.post("/channels/{channel}/test")
def test_channel(channel: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if channel not in ALL_CHANNELS:
        raise HTTPException(status_code=404, detail="Unknown channel")

    row = _get_channel_row(db, current_user.id, channel)
    if not row or not row.enabled:
        raise HTTPException(status_code=400, detail="Enable and save this channel before testing it")

    try:
        dispatch.send(
            db, current_user, channel, row.config, "Candlr test notification", "If you can see this, it works."
        )
    except NotifierError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.get("/webpush/public-key", response_model=VapidPublicKeyOut)
def webpush_public_key():
    return VapidPublicKeyOut(public_key=settings.vapid_public_key)


@router.post("/webpush/subscribe")
def webpush_subscribe(
    data: PushSubscriptionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.vapid_public_key:
        raise HTTPException(status_code=400, detail="Web push is not configured on this instance")

    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == data.endpoint).first()
    if existing:
        existing.user_id = current_user.id
        existing.p256dh = data.keys.p256dh
        existing.auth = data.keys.auth
    else:
        db.add(
            PushSubscription(
                user_id=current_user.id,
                endpoint=data.endpoint,
                p256dh=data.keys.p256dh,
                auth=data.keys.auth,
            )
        )

    # A subscribed device implies this channel is wanted; keep it enabled.
    row = _get_channel_row(db, current_user.id, "webpush")
    if row:
        row.enabled = True
    else:
        db.add(NotificationChannel(user_id=current_user.id, channel="webpush", enabled=True, config={}))
    db.commit()
    return {"ok": True}


@router.post("/webpush/unsubscribe")
def webpush_unsubscribe(
    data: PushSubscriptionDelete,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id, PushSubscription.endpoint == data.endpoint
    ).delete()

    remaining = db.query(PushSubscription).filter(PushSubscription.user_id == current_user.id).count()
    if remaining == 0:
        row = _get_channel_row(db, current_user.id, "webpush")
        if row:
            row.enabled = False

    db.commit()
    return {"ok": True}
