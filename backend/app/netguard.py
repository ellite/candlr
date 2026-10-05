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
so the host checked is the host connected to. A hostname is resolved here and
again by httpx when it connects, so a DNS answer that changes in between
(rebinding) isn't covered."""

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


def check_outbound_url(url: str) -> None:
    """Raises UnsafeURLError unless the server may connect to `url`."""
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
        resolved = {info[4][0] for info in socket.getaddrinfo(host, port)}
    except (OSError, UnicodeError):
        raise UnsafeURLError("Could not resolve that hostname") from None
    if not resolved:
        raise UnsafeURLError("Could not resolve that hostname")

    for ip in resolved:
        addr = ipaddress.ip_address(ip.split("%")[0])
        if any(_unmap(addr) in network for network in _LINK_LOCAL):
            raise UnsafeURLError("That URL points at a link-local address, which is never allowed")
        if not is_public(addr) and not _allowed(host, addr, port):
            shown = f"[{host}]" if ":" in host else host
            raise UnsafeURLError(
                f"{shown}:{port} is on a private or internal network. "
                f"The server admin can allow it by adding {shown}:{port} to INTERNAL_IP_ALLOW_LIST"
            )
