"""Error handling: translated error pages, popup messages and JSON errors.

Every HTTP error ends up in one of three responses:

* JSON ``{"error": message, "status": code}`` for API and ``fetch`` requests
  (the page's JavaScript shows the message as a popup).
* For a failed form submission (POST), a redirect back to the page the form
  was on, with the message as a popup, so the user does not lose their place.
* Otherwise an error page in the app layout, with the message as a popup and
  links back.

Unexpected exceptions are logged with their traceback, the database session
is rolled back and the user sees a generic "something went wrong" message.
"""
import logging

from flask import (
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException, InternalServerError

from app import db
from app.forms import safe_next_url
from app.translations import t

logger = logging.getLogger(__name__)

# Codes with their own title and default message (error_<code>_title / _text).
KNOWN_CODES = frozenset({400, 403, 404, 405, 413, 500})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BYTES_PER_MB = 1024 * 1024


def init_app(app: Flask) -> None:
    """Register the error handlers."""
    app.register_error_handler(HTTPException, handle_http_error)
    # Called for unhandled exceptions (wrapped in InternalServerError) whenever
    # Flask does not propagate them, i.e. outside of tests.
    app.register_error_handler(InternalServerError, handle_internal_error)


def wants_json() -> bool:
    """Whether the client expects a JSON error (API routes and fetch requests)."""
    if request.path.startswith("/api/"):
        return True
    # Browsers ask for text/html; the app's fetch helper sends Accept: application/json.
    return request.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json"


def error_message(error: HTTPException) -> str:
    """Translated message: the description given to abort(), else the code's default text."""
    code = error.code or 500
    if error.description and error.description != type(error).description:
        return error.description
    if code == 413:
        limit = current_app.config.get("MAX_CONTENT_LENGTH") or 0
        return t("error_413_text", round(limit / _BYTES_PER_MB))
    return t(f"error_{code}_text") if code in KNOWN_CODES else t("error_generic_text")


def error_title(code: int) -> str:
    """Translated short title for an HTTP status code."""
    return t(f"error_{code}_title") if code in KNOWN_CODES else t("error_generic_title", code)


def _respond(code: int, message: str) -> ResponseReturnValue:
    """Build the JSON, redirect-with-popup or error-page response."""
    if wants_json():
        return jsonify(error=message, status=code), code
    if request.method not in _SAFE_METHODS and code != 403:
        flash(message, "danger")
        return redirect(safe_next_url(request.referrer, url_for("main.dashboard")))
    return render_template(
        "error.html", code=code, title=error_title(code), message=message
    ), code


def handle_http_error(error: HTTPException) -> ResponseReturnValue:
    """Handle aborts and routing errors (400, 403, 404, 405, 413, ...)."""
    if error.code is None:  # redirects and other non-error HTTPExceptions
        return error
    return _respond(error.code, error_message(error))


def handle_internal_error(error: InternalServerError) -> ResponseReturnValue:
    """Log an unexpected exception and show a generic message."""
    original = error.original_exception or error
    logger.error(
        "Unhandled error on %s %s", request.method, request.path, exc_info=original
    )
    db.session.rollback()
    return _respond(500, t("error_500_text"))
