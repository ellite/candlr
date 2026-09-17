import json

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import NotificationChannel, PushSubscription
from .errors import NotifierError


def send(db: Session, user_id: int, title: str, body: str, *, url: str | None = None) -> None:
    if not settings.vapid_private_key or not settings.vapid_public_key:
        raise NotifierError("Web push is not configured on this instance")

    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    if not subs:
        raise NotifierError("No devices are subscribed to push notifications")

    payload = json.dumps({"title": title, "body": body, "url": url or "/"})
    delivered = 0
    pruned = 0
    errors: list[str] = []

    for sub in subs:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                timeout=10,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
            delivered += 1
        except WebPushException as e:
            status_code = getattr(e.response, "status_code", None)
            if status_code in (404, 410):
                # The browser/OS dropped this subscription; stop targeting it.
                db.delete(sub)
                pruned += 1
            else:
                errors.append(str(e))

    # The session doesn't autoflush (see database.py), so count what's left
    # from what we already know rather than re-querying stale rows.
    remaining = len(subs) - pruned
    if remaining == 0:
        channel = (
            db.query(NotificationChannel)
            .filter(NotificationChannel.user_id == user_id, NotificationChannel.channel == "webpush")
            .first()
        )
        if channel:
            channel.enabled = False
    db.commit()

    if delivered == 0:
        raise NotifierError("; ".join(errors) if errors else "No active devices could be reached")
