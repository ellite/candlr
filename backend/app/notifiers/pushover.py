import httpx

from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    user_key = (config.get("user_key") or "").strip()
    api_token = (config.get("api_token") or "").strip()
    if not user_key or not api_token:
        raise NotifierError("Pushover user key and API token are required")

    try:
        resp = httpx.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": api_token, "user": user_key, "title": title, "message": body},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifierError(f"Pushover error: {e}") from e
