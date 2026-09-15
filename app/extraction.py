"""Receipt field extraction via the Claude vision API.

This module implements the primary backend of the two-stage receipt pipeline:

1. **Claude vision** (this module): the receipt photo is sent to the Anthropic
   API with a JSON schema (structured outputs), returning store, total amount,
   and purchase date, each with a model-reported confidence in [0, 1] and up
   to three alternative candidates. Enabled when an Anthropic API key is
   available (``ANTHROPIC_API_KEY``) or forced via config.
2. **Tesseract OCR** (``app.routes.scan``): local, offline fallback with
   heuristic parsing. Used when the Claude backend is disabled or fails.

Both backends produce the same response shape (see ``EMPTY_RESULT``), so the
mobile confirm form can render confidence badges and candidate chips without
knowing which backend ran.
"""
import base64
import io
import json
import logging
import os
from datetime import date, timedelta

from flask import current_app

logger = logging.getLogger(__name__)

# Long-edge target for images sent to the API. 1568 px is the token-optimal
# vision size; receipts do not need the high-resolution (2576 px) path.
_CLAUDE_MAX_EDGE = 1568
_CLAUDE_JPEG_QUALITY = 85
_CLAUDE_MAX_TOKENS = 2048
_MAX_CANDIDATES = 3
# A receipt date this far in the past (or any future date) is treated as a
# misread and its confidence is capped.
_DATE_PLAUSIBLE_DAYS = 366
_IMPLAUSIBLE_DATE_MAX_CONFIDENCE = 0.3

_RECEIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "store": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "store_confidence": {"type": "number"},
        "store_alternatives": {"type": "array", "items": {"type": "string"}},
        "amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "amount_confidence": {"type": "number"},
        "amount_alternatives": {"type": "array", "items": {"type": "number"}},
        "date": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "date_confidence": {"type": "number"},
    },
    "required": [
        "store", "store_confidence", "store_alternatives",
        "amount", "amount_confidence", "amount_alternatives",
        "date", "date_confidence",
    ],
    "additionalProperties": False,
}

_PROMPT = """\
Extract the following fields from this receipt photo (usually a German store receipt):

- store: the short canonical brand name, e.g. "Aldi", "Lidl", "Rewe", "Rossmann", "DM".
  Use the brand, not the legal entity ("REWE Markt GmbH" -> "Rewe"). null if unreadable.
- amount: the final total paid in euros (the value after "SUMME", "GESAMT", "ZU ZAHLEN"
  or equivalent, after discounts). Never the cash tendered ("BAR") or the change
  ("RÜCKGELD"). null if unreadable.
- date: the purchase date as YYYY-MM-DD. null if unreadable.

For each field report a confidence between 0 and 1 (how certain you are the value is
exactly right). When you are genuinely uncertain, list up to 3 plausible alternatives
in the *_alternatives arrays, most likely first; leave them empty when you are sure."""


def empty_result(backend: str) -> dict:
    """Return the unified extraction response shape with no values filled in."""
    return {
        "store": None,
        "amount": None,
        "date": None,
        "raw": None,
        "backend": backend,
        "fields": {
            "store": {"confidence": 0.0, "candidates": []},
            "amount": {"confidence": 0.0, "candidates": []},
            "date": {"confidence": 0.0, "candidates": []},
        },
    }


def claude_configured() -> bool:
    """Whether the Claude backend should be attempted for the next scan.

    Controlled by ``RECEIPT_EXTRACTION_BACKEND``: ``"tesseract"`` disables it,
    ``"claude"`` forces it, and ``"auto"`` (default) enables it only when an
    ``ANTHROPIC_API_KEY`` is present and the SDK is importable.
    """
    backend = current_app.config.get("RECEIPT_EXTRACTION_BACKEND", "auto")
    if backend == "tesseract":
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        if backend == "claude":
            logger.warning(
                "RECEIPT_EXTRACTION_BACKEND=claude but the 'anthropic' package "
                "is not installed. Run: pip install anthropic"
            )
        return False
    if backend == "claude":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _prepare_image(image_bytes: bytes) -> bytes:
    """Normalise a phone photo for the vision API.

    Applies EXIF rotation, converts to RGB, downscales the long edge to
    ``_CLAUDE_MAX_EDGE`` and re-encodes as JPEG so requests stay small.
    """
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _CLAUDE_MAX_EDGE:
        scale = _CLAUDE_MAX_EDGE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_CLAUDE_JPEG_QUALITY)
    return buf.getvalue()


def _clamp01(value) -> float:
    """Coerce a model-reported confidence to a float in [0, 1]."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalise_claude_result(data: dict, today: date | None = None) -> dict:
    """Map the model's schema-validated JSON onto the unified response shape.

    Args:
        data: Parsed JSON conforming to ``_RECEIPT_SCHEMA``.
        today: Injectable reference date for the date plausibility check.

    Returns:
        A dict in the ``empty_result`` shape with ``backend="claude"``.
        Implausible dates (future, or older than ``_DATE_PLAUSIBLE_DAYS``)
        keep their value but have their confidence capped so the UI flags
        them for review.
    """
    today = today or date.today()
    result = empty_result("claude")

    store = data.get("store")
    if isinstance(store, str) and store.strip():
        result["store"] = store.strip()[:100]
    store_alts = [
        s.strip()[:100] for s in data.get("store_alternatives") or []
        if isinstance(s, str) and s.strip() and s.strip() != result["store"]
    ]
    result["fields"]["store"] = {
        "confidence": _clamp01(data.get("store_confidence")) if result["store"] else 0.0,
        "candidates": store_alts[:_MAX_CANDIDATES],
    }

    amount = data.get("amount")
    if isinstance(amount, (int, float)) and 0 < amount < 100_000:
        result["amount"] = round(float(amount), 2)
    amount_alts = [
        round(float(a), 2) for a in data.get("amount_alternatives") or []
        if isinstance(a, (int, float)) and 0 < a < 100_000 and round(float(a), 2) != result["amount"]
    ]
    result["fields"]["amount"] = {
        "confidence": _clamp01(data.get("amount_confidence")) if result["amount"] is not None else 0.0,
        "candidates": amount_alts[:_MAX_CANDIDATES],
    }

    date_conf = 0.0
    raw_date = data.get("date")
    if isinstance(raw_date, str):
        try:
            parsed = date.fromisoformat(raw_date.strip())
        except ValueError:
            parsed = None
        if parsed is not None:
            result["date"] = parsed.isoformat()
            date_conf = _clamp01(data.get("date_confidence"))
            plausible = today - timedelta(days=_DATE_PLAUSIBLE_DAYS) <= parsed <= today
            if not plausible:
                date_conf = min(date_conf, _IMPLAUSIBLE_DATE_MAX_CONFIDENCE)
    result["fields"]["date"] = {"confidence": date_conf, "candidates": []}

    return result


def extract_with_claude(image_bytes: bytes, client=None) -> tuple[dict | None, str | None]:
    """Extract receipt fields with the Claude vision API.

    Args:
        image_bytes: Raw uploaded image bytes.
        client: Injectable Anthropic client for tests; a real one is
            constructed from the environment when omitted.

    Returns:
        ``(result, None)`` on success, where ``result`` follows the unified
        response shape, or ``(None, error_message)`` when the call failed and
        the caller should fall back to the Tesseract backend.
    """
    import anthropic
    from PIL import Image

    model = current_app.config.get("RECEIPT_EXTRACTION_MODEL", "claude-opus-4-8")
    try:
        prepared = _prepare_image(image_bytes)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # OSError includes PIL.UnidentifiedImageError for non-image uploads.
        return None, f"Image preprocessing failed: {exc}"

    if client is None:
        client = anthropic.Anthropic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=_CLAUDE_MAX_TOKENS,
            output_config={"format": {"type": "json_schema", "schema": _RECEIPT_SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(prepared).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
    except anthropic.AuthenticationError:
        return None, "Anthropic API key invalid or missing."
    except anthropic.RateLimitError:
        return None, "Anthropic API rate limited."
    except anthropic.APIStatusError as exc:
        return None, f"Anthropic API error ({exc.status_code})."
    except anthropic.APIConnectionError:
        return None, "Could not reach the Anthropic API (network)."

    if response.stop_reason == "refusal":
        return None, "Model declined to process the image."

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return None, "Empty model response."
    try:
        data = json.loads(text)
    except ValueError:
        return None, "Model returned malformed JSON."

    result = _normalise_claude_result(data)
    logger.info(
        "Claude extraction: store=%r amount=%r date=%r",
        result["store"], result["amount"], result["date"],
    )
    return result, None
