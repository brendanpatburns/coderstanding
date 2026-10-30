"""Offline guard (docs/DESIGN.md section 18): this process never talks to another machine.

`install()` adds a `sys.addaudithook` hook (it can not be removed again) that refuses every
socket connect / send / bind and every name lookup whose destination is not this PC's
loopback. Allowlist, and nothing else:
- IP addresses in 127.0.0.0/8 and ::1 (also as IPv4-mapped ::ffff:127.x.x.x)
- the loopback names in LOOPBACK_NAMES (any case, trailing dot allowed)
- local IPC sockets (AF_UNIX paths, abstract names), which have no host
- name lookups for no host at all (getaddrinfo(None, port) = loopback)
Everything else raises OfflineError naming the destination. Pure stdlib, no POSIX-only calls
(Windows resolves "localhost" to ::1; both pass). Subprocesses are separate processes: git is
run with GIT_OFFLINE (filewalk.py), and every entry point (CLI, serve, rescan child, scan
workers, tray, Claude hook) calls install() itself.

Stdlib only and imports nothing else from the package (the tray and the Claude hook use it).
"""

import ipaddress
import sys

LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost",
                            "ip6-loopback"})
# git options that keep a local git call local: no transport may run (no fetch, no lazy
# fetch of a partial clone's missing objects) and no repo-configured fsmonitor command.
GIT_OFFLINE = ("-c", "protocol.allow=never", "-c", "core.fsmonitor=false")
GIT_OFFLINE_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}

# audit event -> where its destination is in the event's args
_ADDRESS_EVENTS = {"socket.connect": 1, "socket.sendto": 1, "socket.sendmsg": 1,
                   "socket.bind": 1}
_HOST_EVENTS = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                "socket.gethostbyaddr"}

_installed = False


class OfflineError(PermissionError):
    """A network call to somewhere other than this PC's loopback."""


def is_loopback(host):
    """True for a loopback IP or name; False for anything else (other names never resolve)."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    name = host.strip().strip("[]").split("%", 1)[0].rstrip(".").lower()
    if name in LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _address_ok(address):
    """A socket address: (host, port[, ...]) must be loopback; str/bytes = local IPC (AF_UNIX)."""
    if isinstance(address, (str, bytes, bytearray)) or address is None:
        return True
    if isinstance(address, tuple) and address:
        return is_loopback(address[0])
    return False


def _describe(address):
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return repr(address)


def check(event, args):
    """The audit hook: raise OfflineError for a non-loopback destination."""
    if event in _ADDRESS_EVENTS:
        address = args[_ADDRESS_EVENTS[event]] if len(args) > 1 else None
        if not _address_ok(address):
            raise OfflineError(f"Coderstanding is offline-only: {event} to {_describe(address)} "
                               "refused (only this PC's loopback is allowed)")
    elif event in _HOST_EVENTS:
        host = args[0] if args else None
        if host is not None and not is_loopback(host):
            raise OfflineError(f"Coderstanding is offline-only: {event} for {host!r} refused "
                               "(only this PC's loopback is allowed)")
    elif event == "socket.getnameinfo":
        sockaddr = args[0] if args else None
        if not _address_ok(sockaddr):
            raise OfflineError(f"Coderstanding is offline-only: {event} for "
                               f"{_describe(sockaddr)} refused")


def install():
    """Turn the guard on for this process (idempotent; it stays on for the process's life)."""
    global _installed
    if not _installed:
        sys.addaudithook(check)
        _installed = True


def active():
    return _installed


def helper_listening(host, port, timeout=0.3):
    """True when something listens on host:port (a loopback probe; the guard allows it)."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def privacy_report(host, port, origin, origin_source, listening):
    """`coderstanding privacy`: the current offline state, one line per point, plus a verdict.
    Plain values in, text out (no systemd, /proc or other OS-specific checks)."""
    from_where = f" (from {origin_source})" if origin_source else ""
    lines = [
        "Coderstanding privacy check",
        f"  network guard     {'on' if active() else 'OFF'}: only this PC's loopback "
        "(127.0.0.0/8, ::1, localhost); every other connect or name lookup is refused",
        f"  server bind       {host}:{port}, loopback only (network addresses are refused); "
        f"helper {'running' if listening else 'not running'} there",
        "  browser lockdown  Content-Security-Policy: the viewer page may load and call only "
        "the local server; Referrer-Policy: no-referrer",
        "  allowed origin    " + (f"{origin}{from_where}: that site's page can read and change "
                                  "your maps through the local helper" if origin else
                                  "none: no other site may call the local helper (work setting)"),
        "  speech            on-device voices only (localService); online voices (Google, "
        "Microsoft Online) are never used; no local voice = silent timeline",
        "  git               local commands only (ls-files; protocol.allow=never, no lazy fetch)",
    ]
    if not active():
        verdict = "NOT GUARDED: the network guard is off in this process"
    elif origin:
        verdict = f"offline, except that the hosted page at {origin} may use the local helper"
    else:
        verdict = "fully offline: nothing leaves this PC"
    return "\n".join(lines + [f"Result: {verdict}"])
