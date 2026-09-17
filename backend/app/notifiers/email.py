import smtplib
from email.mime.text import MIMEText

from ..config import settings
from .errors import NotifierError


def send(to_email: str, title: str, body: str, config: dict) -> None:
    if not settings.smtp_address:
        raise NotifierError("SMTP is not configured on this instance")

    msg = MIMEText(body)
    msg["Subject"] = title
    msg["From"] = settings.from_email or settings.smtp_username or "candlr@localhost"
    msg["To"] = to_email

    try:
        if settings.smtp_encryption == "ssl":
            server = smtplib.SMTP_SSL(settings.smtp_address, settings.smtp_port, timeout=10)
        else:
            server = smtplib.SMTP(settings.smtp_address, settings.smtp_port, timeout=10)
        with server:
            if settings.smtp_encryption == "tls":
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.sendmail(msg["From"], [to_email], msg.as_string())
    except Exception as e:
        raise NotifierError(f"SMTP error: {e}") from e
