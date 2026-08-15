"""SSRF / DNS-rebinding guard for tools that fetch arbitrary user-supplied URLs.

Any tool that lets the model (or, on an untrusted interface, anyone who can
reach the model) fetch a URL is a Server-Side Request Forgery hole unless
the target is validated: an attacker names an internal address —
http://169.254.169.254/ (cloud metadata), http://127.0.0.1:6379/ (a local
Redis), http://10.0.0.5/admin — and the agent fetches it from inside the
network on the attacker's behalf.

Naive protection (reject the URL if its literal IP looks private) has a
DNS-rebinding gap: validate example.com at time T0, it resolves public;
by the time the HTTP client actually connects at T1, the attacker's DNS
server has changed the record to 127.0.0.1. The validation and the
connection used two different lookups.

This module closes both:
  resolve_public(url)  — reject if the hostname resolves anywhere private
  pin_host(url)        — context manager that pins the hostname to the
                          exact IP already validated, for the duration of
                          the request, closing the T0/T1 gap.

Usage:
    from relay.net_guard import resolve_public, pin_host

    ok, reason = resolve_public(url)
    if not ok:
        return f"Blocked: {reason}"
    with pin_host(url):
        resp = requests.get(url, timeout=10, allow_redirects=False)
"""

import contextlib
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_private_address(ip_str: str) -> bool:
    """True if an IP (v4 or v6, including IPv4-mapped IPv6) is non-public."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable — treat as unsafe

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def resolve_public(url: str) -> tuple[bool, str]:
    """Validate a URL's scheme and resolve its hostname to confirm all
    addresses are public.

    Returns (ok, reason). reason is empty on success, else a human-readable
    rejection explanation. Does NOT itself close the DNS-rebinding gap —
    combine with pin_host() around the actual request.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "unparseable URL"

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme '{parsed.scheme}' not allowed (only http/https)"

    hostname = parsed.hostname
    if not hostname:
        return False, "no hostname in URL"

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"

    if not infos:
        return False, "DNS resolution returned no addresses"

    resolved_ips = {info[4][0] for info in infos}
    for ip_str in resolved_ips:
        if _is_private_address(ip_str):
            return False, f"hostname '{hostname}' resolves to a private/internal address ({ip_str})"

    return True, ""


@contextlib.contextmanager
def pin_host(url: str):
    """Pin the hostname in `url` to its already-validated IP for the
    duration of the `with` block, closing the DNS-rebinding TOCTOU gap.

    Installs a process-wide shim on socket.getaddrinfo that intercepts
    lookups for this exact hostname and returns the pinned IP; every other
    hostname passes through to the real resolver untouched. Restores the
    original resolver on exit (including on exception).

    Caller is still responsible for calling resolve_public() first — this
    only pins to whatever the real resolver returns right now, it doesn't
    itself validate that the address is public.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        yield
        return

    try:
        infos = socket.getaddrinfo(hostname, None)
        pinned_ip = infos[0][4][0]
    except socket.gaierror:
        yield
        return

    real_getaddrinfo = socket.getaddrinfo

    def _pinned_getaddrinfo(host, *args, **kwargs):
        if host == hostname:
            host = pinned_ip
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _pinned_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo
