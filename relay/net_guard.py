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
  resolve_public(url)         — reject if the hostname resolves anywhere
                                 private; returns the validated public IPs
  pin_host(hostname, ips)     — context manager that constrains DNS
                                 resolution for `hostname` to exactly the
                                 IPs resolve_public() already validated,
                                 for the duration of the request, closing
                                 the T0/T1 gap.

pin_host() deliberately does NOT re-resolve the hostname itself to decide
what to trust — it only ever trusts the `allowed_ips` the caller already
validated. A fresh lookup still happens at actual-connect time (sockets
need real address tuples, not just an IP string), but its results are
filtered down to the pre-validated set; anything outside that set — e.g.
a rebound address — is dropped rather than trusted, and if nothing in the
validated set survives the filter, resolution fails closed (raises
socket.gaierror) rather than silently connecting somewhere unvalidated.

The pinning shim is process-wide (socket.getaddrinfo has no per-request
scope) but keyed by hostname and reference-counted under a lock, so
concurrent pin_host() calls for different hosts don't clobber each other,
and concurrent calls for the SAME host correctly share one shim instead of
one call's exit silently un-pinning a still-active sibling call.

Usage:
    from relay.net_guard import resolve_public, pin_host

    ok, reason, allowed_ips = resolve_public(url)
    if not ok:
        return f"Blocked: {reason}"
    with pin_host(urlparse(url).hostname, allowed_ips):
        resp = requests.get(url, timeout=10, allow_redirects=False)
"""

import contextlib
import ipaddress
import socket
import threading
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


def resolve_public(url: str) -> tuple[bool, str, frozenset[str]]:
    """Validate a URL's scheme and resolve its hostname to confirm all
    addresses are public.

    Returns (ok, reason, allowed_ips). reason is empty on success, else a
    human-readable rejection explanation. allowed_ips is the full set of
    validated public addresses this hostname resolved to — pass it to
    pin_host() so the actual request can't be steered to anything else.
    Does NOT itself close the DNS-rebinding gap — combine with pin_host()
    around the actual request.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "unparseable URL", frozenset()

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme '{parsed.scheme}' not allowed (only http/https)", frozenset()

    hostname = parsed.hostname
    if not hostname:
        return False, "no hostname in URL", frozenset()

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}", frozenset()

    if not infos:
        return False, "DNS resolution returned no addresses", frozenset()

    resolved_ips = frozenset(info[4][0] for info in infos)
    for ip_str in resolved_ips:
        if _is_private_address(ip_str):
            return False, f"hostname '{hostname}' resolves to a private/internal address ({ip_str})", frozenset()

    return True, "", resolved_ips


# --- Process-wide pinning shim: keyed by hostname, reference-counted ---
#
# socket.getaddrinfo has no per-request scope to hook into, so the shim
# has to be global — these structures make concurrent pin_host() calls
# safe: different hostnames don't interfere with each other, and multiple
# concurrent callers pinning the SAME hostname share one shim entry rather
# than one caller's exit silently un-pinning a still-active sibling call.
_pin_lock = threading.Lock()
_pinned_hosts: dict[str, frozenset[str]] = {}
_pin_refcounts: dict[str, int] = {}
# Captured fresh at first-pin time (see pin_host below), NOT at module
# import — capturing it once here at import time would permanently freeze
# a reference to whatever socket.getaddrinfo happened to be at that
# instant, missing any resolver installed afterward (including, notably,
# test mocks applied via unittest.mock.patch after this module is
# imported — but also any other legitimate patch installed at runtime).
_real_getaddrinfo = None
_shim_installed = False


def _pinned_getaddrinfo(host, *args, **kwargs):
    with _pin_lock:
        allowed_ips = _pinned_hosts.get(host)
        real = _real_getaddrinfo
    if allowed_ips is None:
        return real(host, *args, **kwargs)

    infos = real(host, *args, **kwargs)
    filtered = [info for info in infos if info[4][0] in allowed_ips]
    if not filtered:
        raise socket.gaierror(
            f"no previously-validated address remained for '{host}' — "
            f"possible DNS rebinding, refusing to connect"
        )
    return filtered


@contextlib.contextmanager
def pin_host(hostname: str, allowed_ips: frozenset[str]):
    """Constrain DNS resolution for `hostname` to exactly `allowed_ips` —
    the addresses resolve_public() already validated — for the duration of
    the block.

    Does not perform its own independent resolution to decide what's
    trustworthy; it only ever trusts the set the caller already validated.
    If allowed_ips is empty, this is a no-op (nothing to pin to).
    """
    if not hostname or not allowed_ips:
        yield
        return

    global _shim_installed, _real_getaddrinfo
    with _pin_lock:
        if not _shim_installed:
            _real_getaddrinfo = socket.getaddrinfo
            socket.getaddrinfo = _pinned_getaddrinfo
            _shim_installed = True
        _pinned_hosts[hostname] = allowed_ips
        _pin_refcounts[hostname] = _pin_refcounts.get(hostname, 0) + 1

    try:
        yield
    finally:
        with _pin_lock:
            _pin_refcounts[hostname] -= 1
            if _pin_refcounts[hostname] <= 0:
                del _pin_refcounts[hostname]
                _pinned_hosts.pop(hostname, None)
            if not _pin_refcounts and _shim_installed:
                socket.getaddrinfo = _real_getaddrinfo
                _shim_installed = False
