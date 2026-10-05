import httpx

from ..netguard import UnsafeURLError, check_outbound_url
from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    topic = (config.get("topic") or "").strip()
    if not topic:
        raise NotifierError("ntfy topic is not set")

    server = (config.get("server") or "https://ntfy.sh").rstrip("/")
    try:
        check_outbound_url(server)
    except UnsafeURLError as e:
        raise NotifierError(f"ntfy server: {e}") from None
    headers = {"Title": title}
    access_token = (config.get("access_token") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        resp = httpx.post(f"{server}/{topic}", content=body.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifierError(f"ntfy error: {e}") from e
