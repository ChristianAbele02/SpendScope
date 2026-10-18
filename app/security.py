"""Request guards: host check, LAN pairing and CSRF protection.

SpendScope has no user accounts. It is meant for one person on their own PC,
with a phone on the same Wi-Fi for receipt scanning. Three checks run before
every request:

* Host check. Only IP literals, ``localhost`` and this machine's hostname
  (plus ``ALLOWED_HOSTS``) are accepted in the Host header. This blocks DNS
  rebinding, where a website points its own domain at 127.0.0.1 so the
  browser lets it read the local app.
* LAN pairing. Requests from loopback (the PC itself) are always allowed.
  Any other device must first open a URL carrying the pairing token, which
  the QR code on the Add page contains. The token is remembered in that
  device's session cookie. Other devices on the network get 403.
* CSRF. Every POST must carry the session's CSRF token, either as the form
  field ``csrf_token`` or in the ``X-CSRF-Token`` header, so another website
  cannot submit forms to the app from the user's browser.
"""
import hmac
import ipaddress
import secrets
import socket

from flask import Flask, abort, current_app, request, session

from app.translations import t

CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
PAIR_PARAM = "pair"
_CSRF_SESSION_KEY = "csrf_token"
_PAIR_SESSION_KEY = "lan_token"
_TOKEN_BYTES = 32
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOCAL_HOSTNAMES = frozenset({"localhost"})


def init_app(app: Flask) -> None:
    """Generate the pairing token if none is configured and register the guards."""
    if not app.config.get("LAN_TOKEN"):
        app.config["LAN_TOKEN"] = secrets.token_urlsafe(_TOKEN_BYTES)
    app.before_request(_guard)
    app.add_template_global(csrf_token)


def csrf_token() -> str:
    """The session's CSRF token, created on first use (template global)."""
    token = session.get(_CSRF_SESSION_KEY)
    if not token:
        token = session[_CSRF_SESSION_KEY] = secrets.token_urlsafe(_TOKEN_BYTES)
    return token


def pairing_query() -> dict[str, str]:
    """Query arguments that pair a device when added to a URL (used by the QR code)."""
    return {PAIR_PARAM: current_app.config["LAN_TOKEN"]}


def _host_allowed(host: str) -> bool:
    """Whether a Host header value (with or without port) may reach the app."""
    if host.startswith("["):  # IPv6 literal, e.g. "[::1]:5000"
        name = host[1 : host.find("]")]
    else:
        name = host.rsplit(":", 1)[0]
    name = name.lower()
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    allowed = _LOCAL_HOSTNAMES | {socket.gethostname().lower()}
    allowed |= {h.lower() for h in current_app.config.get("ALLOWED_HOSTS", ())}
    return name in allowed


def _is_loopback(addr: str | None) -> bool:
    """Whether the client address belongs to this machine."""
    try:
        return addr is not None and ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _tokens_match(given: object, expected: object) -> bool:
    """Constant-time comparison that is False for missing or non-string values."""
    return (
        isinstance(given, str)
        and isinstance(expected, str)
        and bool(expected)
        and hmac.compare_digest(given.encode(), expected.encode())
    )


def _guard() -> None:
    """Run the host, pairing and CSRF checks; aborts the request on failure."""
    if not _host_allowed(request.host):
        abort(400, description=t("security_host_rejected"))

    if not _is_loopback(request.remote_addr):
        expected = current_app.config["LAN_TOKEN"]
        if _tokens_match(request.args.get(PAIR_PARAM), expected):
            session[_PAIR_SESSION_KEY] = expected
            session.permanent = True
        elif not _tokens_match(session.get(_PAIR_SESSION_KEY), expected):
            abort(403, description=t("security_not_paired"))

    if request.method not in _SAFE_METHODS and current_app.config.get("CSRF_ENABLED", True):
        given = request.form.get(CSRF_FIELD) or request.headers.get(CSRF_HEADER)
        if not _tokens_match(given, session.get(_CSRF_SESSION_KEY)):
            abort(400, description=t("security_csrf_failed"))
