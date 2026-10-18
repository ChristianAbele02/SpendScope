"""Receipt sample database: upload, learned store profiles, images, deletion."""
import io
import os

import pytest

from app.models import ReceiptSample, StoreProfile
from app.routes import scan

RECEIPT_NEXT_LINE = "ALDI SUED\nBrot 2,49\nSUMME EUR\n12,34\n12.03.2024\n"
RECEIPT_SAME_LINE = "ALDI SUED\nMilch 1,19\nSUMME EUR 8,50\n15.03.2024\n"


@pytest.fixture
def fake_ocr(monkeypatch):
    """Replace Tesseract with a queue of fixed OCR results."""
    texts: list[tuple[str | None, str | None]] = []

    def fake(_image_bytes: bytes) -> tuple[str | None, str | None]:
        return texts.pop(0) if texts else (None, "no OCR")

    monkeypatch.setattr(scan, "_try_ocr", fake)
    return texts


def _upload(client, store: str = "Aldi", filename: str = "receipt.jpg"):
    return client.post(
        "/scan/samples/upload",
        data={"store": store, "notes": "Test", "image": (io.BytesIO(b"fake image"), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_samples_page_renders(app):
    assert app.test_client().get("/scan/samples").status_code == 200


def test_upload_extracts_amount_and_learns_profile(app, fake_ocr):
    fake_ocr += [(RECEIPT_NEXT_LINE, None), (RECEIPT_NEXT_LINE, None)]
    client = app.test_client()
    response = _upload(client)
    _upload(client)
    assert "12,34" in response.get_data(as_text=True)
    with app.app_context():
        sample = ReceiptSample.query.first()
        assert sample.extracted_amount == 12.34
        assert sample.amount_on_next_line is True
        profile = StoreProfile.query.filter_by(store="Aldi").one()
        assert profile.sample_count == 2
        assert profile.amount_next_line is True
        assert "SUMME EUR" in profile.total_keywords
        assert os.path.isfile(os.path.join(app.config["RECEIPT_SAMPLES_DIR"], sample.image_filename))


def test_profile_follows_the_majority_layout(app, fake_ocr):
    fake_ocr += [(RECEIPT_SAME_LINE, None), (RECEIPT_SAME_LINE, None), (RECEIPT_NEXT_LINE, None)]
    client = app.test_client()
    for _ in range(3):
        _upload(client)
    with app.app_context():
        assert StoreProfile.query.filter_by(store="Aldi").one().amount_next_line is False


def test_upload_without_ocr_still_saves_with_warning(app, fake_ocr):
    response = _upload(app.test_client())
    assert "text-bg-warning" in response.get_data(as_text=True)
    with app.app_context():
        assert ReceiptSample.query.one().ocr_text is None
        assert StoreProfile.query.count() == 0


@pytest.mark.parametrize("store, filename", [("", "receipt.jpg"), ("Aldi", "evil.html")])
def test_upload_validation(app, fake_ocr, store, filename):
    response = _upload(app.test_client(), store=store, filename=filename)
    assert "text-bg-danger" in response.get_data(as_text=True)
    with app.app_context():
        assert ReceiptSample.query.count() == 0


def test_image_is_served_and_missing_file_gives_404(app, fake_ocr):
    client = app.test_client()
    _upload(client)
    with app.app_context():
        sample = ReceiptSample.query.one()
        path = os.path.join(app.config["RECEIPT_SAMPLES_DIR"], sample.image_filename)
    image = client.get(f"/scan/samples/image/{sample.id}")
    assert image.data == b"fake image"
    image.close()
    os.remove(path)
    assert client.get(f"/scan/samples/image/{sample.id}").status_code == 404


def test_delete_removes_file_and_profile(app, fake_ocr):
    fake_ocr.append((RECEIPT_NEXT_LINE, None))
    client = app.test_client()
    _upload(client)
    with app.app_context():
        sample = ReceiptSample.query.one()
        path = os.path.join(app.config["RECEIPT_SAMPLES_DIR"], sample.image_filename)
    client.post(f"/scan/samples/{sample.id}/delete")
    assert not os.path.exists(path)
    with app.app_context():
        assert ReceiptSample.query.count() == 0
        assert StoreProfile.query.count() == 0


def test_reanalyze_rebuilds_profile(app, fake_ocr):
    fake_ocr.append((RECEIPT_NEXT_LINE, None))
    client = app.test_client()
    _upload(client)
    with app.app_context():
        StoreProfile.query.delete()
        from app import db

        db.session.commit()
    response = client.post("/scan/samples/Aldi/reanalyze", follow_redirects=True)
    assert "text-bg-success" in response.get_data(as_text=True)
    with app.app_context():
        assert StoreProfile.query.filter_by(store="Aldi").one().sample_count == 1


def test_learned_profile_guides_the_parser(app, fake_ocr):
    fake_ocr += [(RECEIPT_NEXT_LINE, None)]
    _upload(app.test_client())
    with app.app_context():
        profile = scan._get_store_profile("Aldi")
    assert profile is not None
    text = "ALDI\nZWISCHENSUMME 3,00\nSUMME EUR\n9,99\n"
    amount, keyword, next_line = scan._parse_amount(
        text, profile["total_keywords"], profile["amount_next_line"]
    )
    assert (amount, keyword, next_line) == (9.99, "SUMME EUR", True)
