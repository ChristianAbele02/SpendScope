"""Validation helpers for user-submitted form values."""
import math
from datetime import date
from urllib.parse import urlparse, urlunparse

from flask import request
from werkzeug.datastructures import MultiDict


def parse_amount(value: str | None) -> float | None:
    """Parse a euro amount that uses either a comma or a dot as decimal separator.

    Returns:
        The amount rounded to cents, or ``None`` for empty, non-numeric or
        non-finite input ("nan", "inf").
    """
    try:
        amount = float((value or "").strip().replace(",", "."))
    except ValueError:
        return None
    return round(amount, 2) if math.isfinite(amount) else None


def parse_iso_date(value: str | None) -> tuple[date | None, bool]:
    """Parse an optional ``YYYY-MM-DD`` form field.

    Returns:
        ``(date_or_None, ok)``. An empty field gives ``(None, True)``; a
        non-empty value that is not a valid date gives ``(None, False)``.
    """
    value = (value or "").strip()
    if not value:
        return None, True
    try:
        return date.fromisoformat(value), True
    except ValueError:
        return None, False


def read_expense_form(form: MultiDict) -> tuple[dict, list[str]]:
    """Read and validate the shared expense fields (add, edit and scan forms).

    Returns:
        ``(values, errors)`` where ``values`` holds ``date``, ``store``,
        ``store_detail``, ``amount`` and ``notes``, and ``errors`` lists
        translation keys for every invalid field (empty when valid).
    """
    store = form.get("store", "").strip()
    amount = parse_amount(form.get("amount"))
    date_val, date_ok = parse_iso_date(form.get("date"))

    errors = []
    if not store:
        errors.append("flash_store_required")
    if amount is None:
        errors.append("flash_invalid_amount")
    if not date_ok:
        errors.append("flash_invalid_date")

    values = {
        "date": date_val,
        "store": store,
        "store_detail": form.get("store_detail", "").strip() or None,
        "amount": amount,
        "notes": form.get("notes", "").strip() or None,
    }
    return values, errors


def safe_next_url(candidate: str | None, fallback: str) -> str:
    """Reduce a redirect target to a path on this site, or return ``fallback``.

    Redirect targets come from hidden ``_next`` fields and the Referer header.
    Both may be absolute URLs of this app (``request.url``), which are reduced
    to path + query; anything pointing at another host is rejected so the app
    cannot be used as an open redirect.
    """
    if not candidate:
        return fallback
    parsed = urlparse(candidate)
    if parsed.scheme not in ("", "http", "https"):
        return fallback
    if parsed.netloc and parsed.netloc != request.host:
        return fallback
    path = parsed.path or "/"
    # "//host" and "/\host" are treated as protocol-relative URLs by browsers.
    if not path.startswith("/") or path.startswith(("//", "/\\")):
        return fallback
    return urlunparse(("", "", path, parsed.params, parsed.query, ""))
