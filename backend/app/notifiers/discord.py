import httpx

from .. import netguard
from ..netguard import UnsafeURLError
from .errors import NotifierError


def send(title: str, body: str, config: dict) -> None:
    webhook_url = (config.get("webhook_url") or "").strip()
    if not webhook_url:
        raise NotifierError("Discord webhook URL is not set")

    try:
        resp = netguard.request("POST", webhook_url, json={"content": f"**{title}**\n{body}"}, timeout=10)
        resp.raise_for_status()
    except UnsafeURLError as e:
        raise NotifierError(f"Discord webhook: {e}") from None
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise NotifierError(f"Discord error: {e}") from e
