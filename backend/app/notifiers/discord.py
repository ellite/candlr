import httpx

from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    webhook_url = (config.get("webhook_url") or "").strip()
    if not webhook_url:
        raise NotifierError("Discord webhook URL is not set")

    try:
        resp = httpx.post(webhook_url, json={"content": f"**{title}**\n{body}"}, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifierError(f"Discord error: {e}") from e
