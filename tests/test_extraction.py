"""Tests for the receipt extraction pipeline (Claude backend + fallback shape)."""
import io
import json
from datetime import date
from types import SimpleNamespace

from app import extraction

TODAY = date(2026, 7, 15)


def _claude_payload(**overrides) -> dict:
    payload = {
        "store": "Rewe",
        "store_confidence": 0.95,
        "store_alternatives": [],
        "amount": 23.45,
        "amount_confidence": 0.9,
        "amount_alternatives": [1.99],
        "date": "2026-07-14",
        "date_confidence": 0.85,
    }
    payload.update(overrides)
    return payload


class _StubClient:
    """Minimal stand-in for anthropic.Anthropic returning a canned response."""

    def __init__(self, payload: dict):
        text_block = SimpleNamespace(type="text", text=json.dumps(payload))
        self._response = SimpleNamespace(stop_reason="end_turn", content=[text_block])
        self.messages = SimpleNamespace(create=lambda **kwargs: self._response)


def _tiny_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 60), color=(255, 255, 255)).save(buf, format="JPEG")
    return buf.getvalue()


# ── _normalise_claude_result ──────────────────────────────────────────────────

def test_normalise_happy_path():
    result = extraction._normalise_claude_result(_claude_payload(), today=TODAY)
    assert result["backend"] == "claude"
    assert result["store"] == "Rewe"
    assert result["amount"] == 23.45
    assert result["date"] == "2026-07-14"
    assert result["fields"]["store"]["confidence"] == 0.95
    assert result["fields"]["amount"]["candidates"] == [1.99]


def test_normalise_clamps_confidence_and_dedupes_candidates():
    payload = _claude_payload(
        store_confidence=7.5,
        amount_alternatives=[23.45, 5.0, 4.0, 3.0, 2.0],  # first duplicates the value
    )
    result = extraction._normalise_claude_result(payload, today=TODAY)
    assert result["fields"]["store"]["confidence"] == 1.0
    # Duplicate of the primary value dropped, list capped at 3
    assert result["fields"]["amount"]["candidates"] == [5.0, 4.0, 3.0]


def test_normalise_null_fields_give_zero_confidence():
    payload = _claude_payload(store=None, amount=None, date=None)
    result = extraction._normalise_claude_result(payload, today=TODAY)
    assert result["store"] is None
    assert result["fields"]["store"]["confidence"] == 0.0
    assert result["fields"]["amount"]["confidence"] == 0.0
    assert result["fields"]["date"]["confidence"] == 0.0


def test_normalise_caps_confidence_for_implausible_date():
    payload = _claude_payload(date="2031-01-01", date_confidence=0.99)
    result = extraction._normalise_claude_result(payload, today=TODAY)
    assert result["date"] == "2031-01-01"  # value kept, but flagged
    assert result["fields"]["date"]["confidence"] <= 0.3


def test_normalise_rejects_malformed_date_and_amount():
    payload = _claude_payload(date="14.07.2026", amount=-5)
    result = extraction._normalise_claude_result(payload, today=TODAY)
    assert result["date"] is None
    assert result["amount"] is None


# ── extract_with_claude (stubbed client) ─────────────────────────────────────

def test_extract_with_claude_stubbed(ctx):
    result, error = extraction.extract_with_claude(_tiny_jpeg(), client=_StubClient(_claude_payload()))
    assert error is None
    assert result["store"] == "Rewe"
    assert result["fields"]["amount"]["confidence"] == 0.9


def test_extract_with_claude_refusal(ctx):
    client = _StubClient(_claude_payload())
    client._response.stop_reason = "refusal"
    result, error = extraction.extract_with_claude(_tiny_jpeg(), client=client)
    assert result is None
    assert error is not None


def test_extract_with_claude_corrupt_image(ctx):
    result, error = extraction.extract_with_claude(b"not an image", client=_StubClient({}))
    assert result is None
    assert "preprocessing" in error.lower()


# ── backend selection ────────────────────────────────────────────────────────

def test_claude_disabled_by_config(ctx, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    ctx.config["RECEIPT_EXTRACTION_BACKEND"] = "tesseract"
    assert extraction.claude_configured() is False


def test_claude_auto_requires_api_key(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctx.config["RECEIPT_EXTRACTION_BACKEND"] = "auto"
    assert extraction.claude_configured() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert extraction.claude_configured() is True


# ── /scan/process route (fallback path, no API key) ──────────────────────────

def test_process_route_returns_unified_shape(app, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = app.test_client()
    resp = client.post(
        "/scan/process",
        data={"image": (io.BytesIO(_tiny_jpeg()), "receipt.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("store", "amount", "date", "backend", "fields", "ocr_available"):
        assert key in data
    assert data["backend"] == "tesseract"
    for field in ("store", "amount", "date"):
        assert "confidence" in data["fields"][field]
        assert "candidates" in data["fields"][field]


def test_process_route_rejects_missing_image(app):
    resp = app.test_client().post("/scan/process", data={})
    assert resp.status_code == 400
