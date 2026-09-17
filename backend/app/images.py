"""Person photo storage: accept an upload or a URL, verify it's really an
image (not just trust the declared content type), normalize it, and store it
under a random filename in settings.images_dir.

The URL path fetches server-side, so it's treated as untrusted input and
guarded against SSRF: only http(s), only to a hostname that resolves
exclusively to public IP addresses (every resolved address is checked, and
DNS is re-resolved rather than trusting a client-supplied IP literal)."""

import io
import ipaddress
import secrets
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .config import settings

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB, applies to both uploads and downloads
MAX_DIMENSION = 800  # longest side, after normalization
ALLOWED_INPUT_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, cloud metadata (169.254.169.254)
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
]


class ImageError(Exception):
    """Raised for any invalid upload/URL/image content; message is user-facing."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparsable -> reject rather than risk it
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return _is_blocked_ip(str(mapped))
    return any(addr in network for network in _BLOCKED_NETWORKS)


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageError("URL must start with http:// or https://")
    hostname = parsed.hostname
    if not hostname:
        raise ImageError("URL has no hostname")

    try:
        resolved = socket.getaddrinfo(hostname, None)
    except OSError:
        raise ImageError("Could not resolve that hostname")

    ips = {info[4][0] for info in resolved}
    if not ips or any(_is_blocked_ip(ip) for ip in ips):
        raise ImageError("That URL points at a private or internal address")


_MAX_REDIRECTS = 5


def download_image(url: str) -> bytes:
    """Fetches url, following redirects manually (never httpx's built-in
    follow_redirects) so every hop is re-validated against the SSRF blocklist
    *before* a connection to it is made, not after."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Candlr/1.0; +https://github.com/ellite/candlr)"}
        with httpx.Client(timeout=10.0, follow_redirects=False, headers=headers) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                _validate_public_url(url)
                resp = client.get(url)
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ImageError("Redirect had no location")
                    url = str(httpx.URL(url).join(location))
                    continue
                resp.raise_for_status()

                total = 0
                chunks = []
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ImageError("Image is too large (max 8 MB)")
                    chunks.append(chunk)
                return b"".join(chunks)
            raise ImageError("Too many redirects")
    except ImageError:
        raise
    except httpx.HTTPError as e:
        raise ImageError(f"Could not download that image: {e}") from e


def normalize_image(data: bytes) -> tuple[bytes, str]:
    """Verifies the bytes are a real image and re-encodes them, which both
    guards against disguised non-image payloads and strips any metadata."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))  # verify() consumes the parser; reopen to actually use it
        if img.format not in ALLOWED_INPUT_FORMATS:
            raise ImageError("Unsupported image format. Use JPEG, PNG, WEBP or GIF.")
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ImageError("That doesn't look like a valid image")

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")

    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    out = io.BytesIO()
    if img.mode == "RGBA":
        img.save(out, format="PNG", optimize=True)
        ext = "png"
    else:
        img.save(out, format="JPEG", quality=85, optimize=True)
        ext = "jpg"
    return out.getvalue(), ext


def save_image(data: bytes) -> str:
    """Normalizes and writes the image to disk, returning its filename."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("Image is too large (max 8 MB)")
    normalized, ext = normalize_image(data)
    filename = f"{secrets.token_hex(16)}.{ext}"
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    (settings.images_dir / filename).write_bytes(normalized)
    return filename


def delete_image(filename: str) -> None:
    if not filename:
        return
    path = settings.images_dir / Path(filename).name  # .name strips any path components
    path.unlink(missing_ok=True)
