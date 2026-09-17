import httpx

from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    bot_token = (config.get("bot_token") or "").strip()
    chat_id = (config.get("chat_id") or "").strip()
    if not bot_token or not chat_id:
        raise NotifierError("Telegram bot token and chat ID are required")

    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{title}\n{body}"},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifierError(f"Telegram error: {e}") from e
