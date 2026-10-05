"""Guard for every URL a user can point the server at (photo URLs, CardDAV
address books, ntfy / Discord / Gotify servers), so the server can't be used
to reach services on its own network.

Anything that resolves to a public address is allowed. Addresses that aren't
public (loopback, private ranges, shared address space, ...) are refused
unless the admin lists them in INTERNAL_IP_ALLOW_LIST, a comma separated list
of entries like

    10.0.0.5            one address, any port
    10.0.0.5:3001       one address, that port only
    192.168.1.0/24      a network
    gotify:80           a hostname (a Docker service name, say), that port only
    [fd00::5]:8080      an IPv6 address with a port

A hostname entry matches the hostname in the URL; the other entries match the
address it resolves to. Every resolved address must pass. Link-local
addresses (cloud metadata endpoints) are never allowed, even if listed.

The URL is parsed with httpx.URL, the parser that makes the actual request,
so the host checked is the host connected to.

check_outbound_url() only validates, which is right for refusing a URL when a
setting is saved. To make a request, use request() or stream(): they resolve
the hostname once, validate every address, and then connect to one of those
exact addresses (sending the original hostname as the Host header and for TLS
verification), so a DNS answer that changes between the check and the
connection (rebinding) can't redirect the request. Each call uses a fresh
client and never follows redirects; a caller that follows them must call
again for every hop."""

import contextlib
import ipaddress
import logging
import socket
from dataclasses import dataclass
from functools import lru_cache

import httpx

from .config import settings

log = logging.getLogger(__name__)

_LINK_LOCAL = [ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10")]
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeURLError(ValueError):
    """The URL can't be used; the message is shown to the user."""


@dataclass(frozen=True)
class Rule:
    host: str | None  # lowercase hostname, or None for an address/network rule
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None
    port: int | None  # None means any port


def _parse_entry(entry: str) -> Rule:
    port = None
    target = entry
    if entry.startswith("["):
        closing = entry.find("]")
        if closing == -1:
            raise ValueError("missing ]")
        target, rest = entry[1:closing], entry[closing + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise ValueError("unexpected text after ]")
            port = rest[1:]
    elif entry.count(":") == 1:
        target, port = entry.split(":")
    # More than one colon without brackets is a bare IPv6 address or network.
    if port is not None:
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError("the port must be a number from 1 to 65535")
        port = int(port)
    if not target:
        raise ValueError("empty host")
    try:
        return Rule(host=None, network=ipaddress.ip_network(target, strict=False), port=port)
    except ValueError:
        pass
    host = target.lower().rstrip(".")
    if not all(c.isascii() and (c.isalnum() or c in "-._") for c in host):
        raise ValueError("not an address, network or hostname")
    return Rule(host=host, network=None, port=port)


@lru_cache(maxsize=8)
def _parse_list(raw: str) -> tuple[Rule, ...]:
    rules = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            rules.append(_parse_entry(entry))
        except ValueError as e:
            log.warning("Ignoring INTERNAL_IP_ALLOW_LIST entry %r: %s", entry, e)
    return tuple(rules)


def allow_rules() -> tuple[Rule, ...]:
    return _parse_list(settings.internal_ip_allow_list)


def _unmap(addr):
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def is_public(addr) -> bool:
    addr = _unmap(addr)
    return addr.is_global and not addr.is_multicast


def _allowed(host: str, addr, port: int) -> bool:
    for rule in allow_rules():
        if rule.port is not None and rule.port != port:
            continue
        if rule.host is not None:
            if rule.host == host:
                return True
        elif addr in rule.network or _unmap(addr) in rule.network:
            return True
    return False


def _resolve(url: str):
    """Validates `url` and returns (parsed, host, port, addresses): the
    httpx.URL, its lowercase hostname, its port (the scheme's default if the
    URL has none), and the addresses it resolves to, all of which passed."""
    try:
        parsed = httpx.URL(url)
        port = parsed.port or _DEFAULT_PORTS.get(parsed.scheme)
    except httpx.InvalidURL:
        raise UnsafeURLError("That is not a valid URL") from None
    if parsed.scheme not in _DEFAULT_PORTS:
        raise UnsafeURLError("The URL must start with http:// or https://")
    host = parsed.host.lower().rstrip(".")
    if not host:
        raise UnsafeURLError("The URL has no hostname")

    try:
        infos = socket.getaddrinfo(host, port)
    except (OSError, UnicodeError):
        raise UnsafeURLError("Could not resolve that hostname") from None
    # In the resolver's preference order, without duplicates.
    addresses = list(dict.fromkeys(info[4][0].split("%")[0] for info in infos))
    if not addresses:
        raise UnsafeURLError("Could not resolve that hostname")

    for ip in addresses:
        addr = ipaddress.ip_address(ip)
        if any(_unmap(addr) in network for network in _LINK_LOCAL):
            raise UnsafeURLError("That URL points at a link-local address, which is never allowed")
        if not is_public(addr) and not _allowed(host, addr, port):
            shown = f"[{host}]" if ":" in host else host
            raise UnsafeURLError(
                f"{shown}:{port} is on a private or internal network. "
                f"The server admin can allow it by adding {shown}:{port} to INTERNAL_IP_ALLOW_LIST"
            )
    return parsed, host, port, addresses


def check_outbound_url(url: str) -> None:
    """Raises UnsafeURLError unless the server may connect to `url`."""
    _resolve(url)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@contextlib.contextmanager
def stream(method: str, url: str, *, headers=None, auth=None, timeout: float = 10.0, transport=None, **request_kwargs):
    """Makes a request to `url` and yields the response with its body unread,
    connecting to a validated address (see the module docstring). Raises
    UnsafeURLError if the URL isn't allowed, and httpx errors as usual. If a
    hostname has several addresses, the next is tried when a connection fails.

    `request_kwargs` (content, json, data, params, ...) go to httpx's
    build_request. `transport` is for tests."""
    parsed, host, port, addresses = _resolve(url)
    pin = not _is_ip(host)  # an IP literal needs neither a Host header nor SNI
    base_extensions = dict(request_kwargs.pop("extensions", None) or {})
    last_error: Exception | None = None
    for ip in addresses:
        client = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport)
        try:
            request_headers = httpx.Headers(headers or {})
            extensions = dict(base_extensions)
            if pin:
                shown = f"[{host}]" if ":" in host else host
                request_headers.setdefault("Host", f"{shown}:{parsed.port}" if parsed.port else shown)
                if parsed.scheme == "https":
                    # TLS verifies the certificate against this name, not the IP.
                    extensions["sni_hostname"] = host
            request = client.build_request(
                method, parsed.copy_with(host=ip), headers=request_headers, extensions=extensions, **request_kwargs
            )
            response = client.send(request, auth=auth, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            client.close()
            last_error = e
            continue
        except BaseException:
            client.close()
            raise
        # Errors and logs should name the URL that was asked for, not the
        # address it was pinned to.
        response.request.url = httpx.URL(url)
        try:
            yield response
        finally:
            response.close()
            client.close()
        return
    raise last_error


def request(method: str, url: str, **kwargs) -> httpx.Response:
    """Like stream(), but reads the whole body before returning."""
    with stream(method, url, **kwargs) as response:
        response.read()
        return response
