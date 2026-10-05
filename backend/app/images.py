"""Person photo storage: accept an upload or a URL, verify it's really an
image (not just trust the declared content type), normalize it, and store it
under a random filename in settings.images_dir.

The URL path fetches server-side, so it's treated as untrusted input and
guarded against SSRF by netguard.check_outbound_url: only http(s), and only
to public addresses unless the admin allows an internal one."""

import io
import secrets
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from .config import settings
from . import netguard
from .netguard import UnsafeURLError, check_outbound_url

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB, applies to both uploads and downloads
MAX_DIMENSION = 800  # longest side, after normalization
ALLOWED_INPUT_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}

class ImageError(Exception):
    """Raised for any invalid upload/URL/image content; message is user-facing."""


def _validate_public_url(url: str) -> None:
    try:
        check_outbound_url(url)
    except UnsafeURLError as e:
        raise ImageError(str(e)) from None


_MAX_REDIRECTS = 5


def download_image(url: str) -> bytes:
    """Fetches url, following redirects manually (never httpx's built-in
    follow_redirects) so every hop is validated and pinned to a checked
    address *before* a connection to it is made (see netguard.py). The size
    cap is enforced while reading, so an oversized body is never held in
    memory."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Candlr/1.0; +https://github.com/ellite/candlr)"}
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            with netguard.stream("GET", url, headers=headers, timeout=10.0) as resp:
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
    except netguard.UnsafeURLError as e:
        raise ImageError(str(e)) from None
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
