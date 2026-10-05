import httpx

from .errors import NotifierError

MAX_PRIORITY = 10


def parse_config(config: dict) -> tuple[str, str, int | None]:
    """Returns (server, app_token, priority) from a Gotify channel config, or
    raises ValueError with a user-facing message. Shared by the save endpoint
    and the sender so both accept exactly the same values."""
    server = (config.get("server") or "").strip().rstrip("/")
    app_token = (config.get("app_token") or "").strip()
    if not server or not app_token:
        raise ValueError("Gotify server URL and app token are required")
    try:
        url = httpx.URL(server)
    except httpx.InvalidURL:
        raise ValueError("Gotify server is not a valid URL") from None
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError("Gotify server must be an http:// or https:// URL")

    raw_priority = config.get("priority")
    text = "" if raw_priority is None else str(raw_priority).strip()
    priority = None
    if text:
        # isdecimal() plus isascii(): str.isdigit() also accepts characters
        # like "²" that int() then refuses.
        if not (text.isascii() and text.isdecimal()) or int(text) > MAX_PRIORITY:
            raise ValueError(f"Gotify priority must be a whole number from 0 to {MAX_PRIORITY}")
        priority = int(text)
    return server, app_token, priority


def send(title: str, body: str, config: dict) -> None:
    try:
        server, app_token, priority = parse_config(config)
    except ValueError as e:
        raise NotifierError(str(e)) from None

    payload = {"title": title, "message": body}
    if priority is not None:
        payload["priority"] = priority

    try:
        resp = httpx.post(
            f"{server}/message",
            json=payload,
            headers={"X-Gotify-Key": app_token},
            timeout=10,
        )
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise NotifierError(f"Gotify error: {e}") from e
