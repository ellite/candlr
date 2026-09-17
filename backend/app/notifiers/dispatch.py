from sqlalchemy.orm import Session

from ..models import User
from . import email as email_notifier
from . import ntfy as ntfy_notifier
from . import discord as discord_notifier
from . import telegram as telegram_notifier
from . import pushover as pushover_notifier
from . import webpush as webpush_notifier
from .errors import NotifierError

_SENDERS = {
    "email": lambda db, user, title, body, config: email_notifier.send(user.email, title, body, config),
    "ntfy": lambda db, user, title, body, config: ntfy_notifier.send(title, body, config),
    "discord": lambda db, user, title, body, config: discord_notifier.send(title, body, config),
    "telegram": lambda db, user, title, body, config: telegram_notifier.send(title, body, config),
    "pushover": lambda db, user, title, body, config: pushover_notifier.send(title, body, config),
}


def send(db: Session, user: User, channel: str, config: dict, title: str, body: str, *, url: str | None = None) -> None:
    """Sends one message on one channel. Raises NotifierError on failure.

    Used both by the settings page's "send test" button, and by the
    reminder worker.
    """
    if channel == "webpush":
        webpush_notifier.send(db, user.id, title, body, url=url)
        return
    sender = _SENDERS.get(channel)
    if not sender:
        raise NotifierError(f"Unknown channel: {channel}")
    sender(db, user, title, body, config or {})
