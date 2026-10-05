import httpx

from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    server = (config.get("server") or "").strip().rstrip("/")
    app_token = (config.get("app_token") or "").strip()
    if not server or not app_token:
        raise NotifierError("Gotify server URL and app token are required")

    payload = {"title": title, "message": body}
    raw_priority = config.get("priority")
    priority = "" if raw_priority is None else str(raw_priority).strip()
    if priority:
        payload["priority"] = int(priority)

    try:
        resp = httpx.post(
            f"{server}/message",
            json=payload,
            headers={"X-Gotify-Key": app_token},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifierError(f"Gotify error: {e}") from e
