"""Receipt scanning and the receipt sample database.

Scan flow:
  1. The desktop shows a QR code (/scan/qr, or inline on /add) that encodes
     http://<LAN IP>:<port>/scan/
  2. The phone opens /scan/ and takes a photo of the receipt
  3. The phone POSTs the image to /scan/process and receives the extracted
     fields as JSON (Claude vision when configured, Tesseract OCR otherwise;
     see app.extraction for the shared response shape)
  4. The phone submits the confirmed form to /scan/save

The sample database (/scan/samples) stores reference receipts per store and
learns which total keyword and line layout each store uses (StoreProfile).
Learned profiles are applied by the Tesseract parser only.
"""
import io
import json
import os
import re
import socket
import uuid
from datetime import UTC, date, datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app import db, extraction
from app.forms import read_expense_form
from app.models import Expense, ReceiptSample, StoreProfile
from app.parser import get_category
from app.translations import format_eur, t

scan = Blueprint("scan", __name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Any routable address works: connecting a UDP socket sends no packets, it only
# makes the OS pick the outgoing interface, whose address is the LAN IP.
_LAN_PROBE_ADDR = ("8.8.8.8", 80)
_DEFAULT_PORT = 5000

# OCR preprocessing
_MAX_IMAGE_PIXELS = 50_000_000   # refuse decompression bombs (a phone photo is ~12 MP)
_OCR_MAX_EDGE = 2000             # Tesseract gains nothing from larger images
_OCR_CONTRAST = 2.0
_OCR_BINARY_THRESHOLD = 160      # grey level separating ink from off-white paper
_TESSERACT_CONFIG = "--psm 4 --oem 3"  # PSM 4: single column of variable-size text
_TESSERACT_LANGS = "deu+eng"

# Receipt parsing
_AMOUNT_RE = re.compile(r"(\d{1,4}[,\.]\d{2})")
_MIN_PLAUSIBLE_AMOUNT = 0.5
_MAX_PLAUSIBLE_AMOUNT = 9999.0
_STORE_SEARCH_LINES = 25         # known store names are looked for in the header
_STORE_FALLBACK_LINES = 8
_STORE_NAME_MAX_LEN = 60
_RECEIPT_DATE_MAX_AGE_DAYS = 90
_MAX_AMOUNT_CANDIDATES = 3

# Heuristic confidences for the Tesseract backend (Claude reports its own).
_CONF_STORE_KNOWN = 0.8      # store matched against _KNOWN_STORES
_CONF_STORE_GUESS = 0.3      # first substantial text line fallback
_CONF_AMOUNT_KEYWORD = 0.7   # amount found next to a total keyword
_CONF_AMOUNT_FALLBACK = 0.4  # largest plausible amount in the document
_CONF_DATE_FOUND = 0.7

# Store profile learning: amounts count as "next line" when most hits are there.
_NEXT_LINE_MAJORITY = 0.5

# Extensions accepted for sample uploads; anything else could be served back
# as HTML by send_file and is rejected.
_ALLOWED_SAMPLE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff",
}

# Known store names to look for in OCR text, longest first so that e.g.
# "Marktkauf" is tried before the short "DM".
_KNOWN_STORES = sorted([
    "Müller", "Mueller", "Aldi", "Lidl", "Rewe", "Penny", "Kaufland",
    "Edeka", "Marktkauf", "Combi", "Netto", "Rossmann", "DM", "Action",
    "Amazon", "Ikea", "Jysk", "OBI", "Toom", "Waschstraße", "Subway",
], key=len, reverse=True)

# Total keywords evaluated when learning a store profile, most specific first.
_CANDIDATE_KEYWORDS = [
    "GESAMTBETRAG", "ENDBETRAG", "ZU ZAHLEN", "SUMME EUR",
    "GESAMT EUR", "GESAMT", "SUMME", "TOTAL EUR", "TOTAL",
    "BARGELD", "BAR EUR", "BAR",
]

_GENERIC_KW_RE = re.compile(
    r"(?:GES(?:AMT)?(?:BETRAG)?|SUMME|ZU\s*ZAHLEN|TOTAL|ENDBETRAG|BARGELD|BAR(?:\s*EUR)?)",
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Best-effort detection of this machine's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(_LAN_PROBE_ADDR)
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _scan_url() -> str:
    """URL of the mobile scan page as reachable from a phone on the same network."""
    port = request.environ.get("SERVER_PORT", _DEFAULT_PORT)
    return f"http://{_local_ip()}:{port}{url_for('scan.mobile')}"


def _try_ocr(image_bytes: bytes) -> tuple[str | None, str | None]:
    """Run Tesseract OCR on a receipt photo.

    The image is EXIF-rotated, converted to greyscale, downscaled, contrast
    boosted, sharpened and binarised before recognition.

    Returns:
        ``(text, None)`` on success or ``(None, error_message)`` on failure.
    """
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as e:
        return None, f"Missing Python package: {e}. Run: pip install pytesseract Pillow"

    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS

    tesseract_cmd = current_app.config.get("TESSERACT_CMD")
    if tesseract_cmd and os.path.isfile(tesseract_cmd):
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # decode now so oversized or corrupt images fail here
        img = ImageOps.exif_transpose(img).convert("L")

        w, h = img.size
        if max(w, h) > _OCR_MAX_EDGE:
            scale = _OCR_MAX_EDGE / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        img = ImageEnhance.Contrast(img).enhance(_OCR_CONTRAST).filter(ImageFilter.SHARPEN)
        img = img.point(lambda p: 0 if p < _OCR_BINARY_THRESHOLD else 255)

        try:
            text = pytesseract.image_to_string(img, lang=_TESSERACT_LANGS, config=_TESSERACT_CONFIG)
        except pytesseract.TesseractError:
            # German language data not installed: retry with English only.
            text = pytesseract.image_to_string(img, lang="eng", config=_TESSERACT_CONFIG)
        return text, None
    # OSError covers unreadable images and a missing Tesseract binary;
    # RuntimeError covers TesseractError and timeouts.
    except (OSError, RuntimeError, ValueError, Image.DecompressionBombError) as e:
        return None, str(e)


def _to_amount(raw: str) -> float:
    """Convert a regex match like ``"12,34"`` to a float."""
    return round(float(raw.replace(",", ".")), 2)


def _plausible_amounts(text: str) -> list[float]:
    """All distinct plausible amounts in the text, largest first."""
    values = {_to_amount(raw) for raw in _AMOUNT_RE.findall(text or "")}
    return sorted(
        (v for v in values if _MIN_PLAUSIBLE_AMOUNT <= v <= _MAX_PLAUSIBLE_AMOUNT),
        reverse=True,
    )


def _find_amount_near_keyword(
    lines: list[str], keyword_re: re.Pattern, prefer_next_line: bool
) -> tuple[float | None, str | None, bool]:
    """Find the first keyword line with an amount on it or on the line below.

    Returns:
        ``(amount, matched_keyword_upper, amount_was_on_next_line)``, or
        ``(None, None, False)`` if no keyword line has an amount nearby.
    """
    for i, line in enumerate(lines):
        match = keyword_re.search(line)
        if not match:
            continue
        same_line = (line, False)
        next_line = (lines[i + 1], True) if i + 1 < len(lines) else None
        for candidate in ([next_line, same_line] if prefer_next_line else [same_line, next_line]):
            if candidate is None:
                continue
            amount_match = _AMOUNT_RE.search(candidate[0])
            if amount_match:
                return _to_amount(amount_match.group(1)), match.group(0).upper(), candidate[1]
    return None, None, False


def _parse_amount(
    text: str,
    preferred_keywords: list | None = None,
    prefer_next_line: bool = False,
) -> tuple[float | None, str | None, bool]:
    """Extract the total amount from receipt text.

    Strategy: learned profile keywords first, then generic German total
    keywords, then the largest plausible amount anywhere in the text.

    Args:
        text: Raw OCR text.
        preferred_keywords: Keywords from the store's StoreProfile, in priority order.
        prefer_next_line: Whether this store prints the amount below the keyword.

    Returns:
        ``(amount, keyword_used, was_on_next_line)``; the keyword is ``None``
        when the largest-amount fallback was used.
    """
    lines = text.splitlines()

    for kw in preferred_keywords or []:
        amount, _, next_line = _find_amount_near_keyword(
            lines, re.compile(re.escape(kw), re.IGNORECASE), prefer_next_line
        )
        if amount is not None:
            return amount, kw, next_line

    amount, keyword, next_line = _find_amount_near_keyword(lines, _GENERIC_KW_RE, prefer_next_line)
    if amount is not None:
        return amount, keyword, next_line

    candidates = _plausible_amounts(text)
    return (candidates[0], None, False) if candidates else (None, None, False)


def _parse_date_from_text(text: str, today: date | None = None) -> date | None:
    """Most recent plausible receipt date (DD.MM.YYYY or DD.MM.YY, any of ./-).

    Only dates within the last ``_RECEIPT_DATE_MAX_AGE_DAYS`` days are accepted,
    which filters out misread digits and printed expiry or loyalty dates.
    """
    today = today or date.today()
    patterns = (
        (re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b"), 0),
        (re.compile(r"\b(\d{2})[./\-](\d{2})[./\-](\d{2})\b"), 2000),
    )
    candidates = []
    for pattern, century in patterns:
        for m in pattern.finditer(text):
            try:
                d = date(century + int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                continue
            if 0 <= (today - d).days <= _RECEIPT_DATE_MAX_AGE_DAYS:
                candidates.append(d)
    return max(candidates) if candidates else None


def _parse_receipt(text: str) -> dict:
    """Extract store, amount and date from the OCR text of a German receipt.

    All fields are best-effort; missing ones are ``None``. ``store_known`` and
    ``keyword_found`` feed the heuristic confidences in ``_tesseract_fields``.
    """
    result: dict = {
        "store": None, "amount": None, "date": None, "raw": text,
        "keyword_found": None, "store_known": False,
    }
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return result

    # Store: a known brand in the header, else the first non-numeric line.
    for line in lines[:_STORE_SEARCH_LINES]:
        store = next((s for s in _KNOWN_STORES if s.lower() in line.lower()), None)
        if store:
            result["store"], result["store_known"] = store, True
            break
    if not result["store"]:
        for line in lines[:_STORE_FALLBACK_LINES]:
            if len(line) >= 3 and not re.match(r"^[\d\s\.\,\-\+\*\/\(\)€]+$", line):
                result["store"] = line[:_STORE_NAME_MAX_LEN]
                break

    profile = _get_store_profile(result["store"])
    result["amount"], result["keyword_found"], _ = _parse_amount(
        text,
        profile["total_keywords"] if profile else None,
        profile["amount_next_line"] if profile else False,
    )

    receipt_date = _parse_date_from_text(text)
    result["date"] = receipt_date.isoformat() if receipt_date else None
    return result


def _tesseract_fields(parsed: dict, text: str) -> dict:
    """Map a ``_parse_receipt`` result onto the unified extraction shape.

    Adds heuristic per-field confidences and up to ``_MAX_AMOUNT_CANDIDATES``
    alternative amounts (other plausible values in the text, largest first)
    that the phone form shows as tappable chips.
    """
    result = extraction.empty_result("tesseract")
    for key in ("store", "amount", "date", "raw"):
        result[key] = parsed.get(key)

    fields = result["fields"]
    if result["store"]:
        fields["store"]["confidence"] = (
            _CONF_STORE_KNOWN if parsed.get("store_known") else _CONF_STORE_GUESS
        )
    if result["amount"] is not None:
        fields["amount"]["confidence"] = (
            _CONF_AMOUNT_KEYWORD if parsed.get("keyword_found") else _CONF_AMOUNT_FALLBACK
        )
    if result["date"]:
        fields["date"]["confidence"] = _CONF_DATE_FOUND

    alternatives = [v for v in _plausible_amounts(text) if v != result["amount"]]
    fields["amount"]["candidates"] = alternatives[:_MAX_AMOUNT_CANDIDATES]
    return result


# ── Scan routes ───────────────────────────────────────────────────────────────

@scan.route("/")
def mobile():
    """Mobile receipt capture page."""
    return render_template("scan_mobile.html")


@scan.route("/qr")
def qr_page():
    """Desktop page showing a large QR code for the scan URL."""
    return render_template("scan_qr.html", scan_url=_scan_url())


@scan.route("/qr-image")
def qr_image():
    """QR code PNG for the scan URL, generated server-side with the LAN IP."""
    import qrcode

    qr = qrcode.QRCode(box_size=10, border=3)
    qr.add_data(_scan_url())
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})


@scan.route("/process", methods=["POST"])
def process():
    """Extract expense fields from an uploaded receipt image.

    Tries the Claude vision backend first (when configured), then falls back
    to local Tesseract OCR. Returns JSON in the unified extraction shape:
    ``{store, amount, date, raw, backend, fields: {store|amount|date:
    {confidence, candidates}}, ocr_available, ocr_error, llm_error}``.
    """
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    image_bytes = request.files["image"].read()

    llm_error = None
    if extraction.claude_configured():
        result, llm_error = extraction.extract_with_claude(image_bytes)
        if result is not None:
            result.update(ocr_available=True, ocr_error=None, llm_error=None)
            return jsonify(result)

    ocr_text, ocr_error = _try_ocr(image_bytes)
    if ocr_text is not None:
        result = _tesseract_fields(_parse_receipt(ocr_text), ocr_text)
    else:
        result = extraction.empty_result("tesseract")

    result.update(ocr_available=ocr_text is not None, ocr_error=ocr_error, llm_error=llm_error)
    return jsonify(result)


@scan.route("/save", methods=["POST"])
def save():
    """Save the expense confirmed on the phone."""
    values, errors = read_expense_form(request.form)
    if errors:
        for key in errors:
            flash(t(key), "danger")
        return redirect(url_for("scan.mobile"))

    values["category"] = get_category(values["store"], values["store_detail"])
    db.session.add(Expense(**values))
    db.session.commit()
    flash(t("flash_expense_added", format_eur(values["amount"]), values["store"]), "success")
    return redirect(url_for("main.dashboard"))


# ── Receipt sample database ───────────────────────────────────────────────────

def _samples_dir() -> str:
    """Return (and create) the directory holding uploaded sample images."""
    path = current_app.config["RECEIPT_SAMPLES_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def _build_store_profile(store: str) -> None:
    """Re-learn the StoreProfile for ``store`` from all its OCR-processed samples.

    For every candidate keyword, counts the samples in which it appears with
    an amount on the same or the following line. Keywords are stored by hit
    count, and ``amount_next_line`` is set when most hits were on the next
    line. The profile is deleted when no samples remain.
    """
    samples = (
        ReceiptSample.query
        .filter_by(store=store)
        .filter(ReceiptSample.ocr_text.isnot(None))
        .all()
    )
    profile = StoreProfile.query.filter_by(store=store).first()

    if not samples:
        if profile:
            db.session.delete(profile)
            db.session.commit()
        return

    hits = {kw: {"total": 0, "next_line": 0} for kw in _CANDIDATE_KEYWORDS}
    for sample in samples:
        lines = sample.ocr_text.splitlines()
        for kw in _CANDIDATE_KEYWORDS:
            amount, _, next_line = _find_amount_near_keyword(
                lines, re.compile(re.escape(kw), re.IGNORECASE), prefer_next_line=False
            )
            if amount is not None:
                hits[kw]["total"] += 1
                hits[kw]["next_line"] += int(next_line)

    successful = sorted(
        ((kw, h) for kw, h in hits.items() if h["total"]),
        key=lambda item: item[1]["total"],
        reverse=True,
    )
    total_votes = sum(h["total"] for _, h in successful)
    next_line_votes = sum(h["next_line"] for _, h in successful)

    if not profile:
        profile = StoreProfile(store=store)
        db.session.add(profile)
    profile.total_keywords_json = json.dumps([kw for kw, _ in successful])
    profile.amount_next_line = bool(total_votes and next_line_votes / total_votes > _NEXT_LINE_MAJORITY)
    profile.sample_count = len(samples)
    profile.last_updated = datetime.now(UTC)
    db.session.commit()


def _get_store_profile(store: str | None) -> dict | None:
    """Learned parsing rules for ``store``, or ``None`` if there are none yet."""
    if not store:
        return None
    p = StoreProfile.query.filter_by(store=store).first()
    if not p or not p.total_keywords:
        return None
    return {"total_keywords": p.total_keywords, "amount_next_line": p.amount_next_line}


@scan.route("/samples")
def samples():
    """Sample upload form, learned profiles and the sample gallery."""
    all_samples = ReceiptSample.query.order_by(ReceiptSample.uploaded_at.desc()).all()
    return render_template(
        "scan_samples.html",
        samples=all_samples,
        profiles=StoreProfile.query.order_by(StoreProfile.store).all(),
        all_stores=sorted({sample.store for sample in all_samples}),
    )


@scan.route("/samples/upload", methods=["POST"])
def upload_sample():
    """Store a reference receipt, OCR it and rebuild the store's profile."""
    store = request.form.get("store", "").strip()
    notes = request.form.get("notes", "").strip() or None
    image_file = request.files.get("image")

    if not store or not image_file or not image_file.filename:
        flash(t("flash_sample_required"), "danger")
        return redirect(url_for("scan.samples"))

    ext = os.path.splitext(image_file.filename)[1].lower() or ".jpg"
    if ext not in _ALLOWED_SAMPLE_EXTENSIONS:
        flash(t("flash_sample_bad_type"), "danger")
        return redirect(url_for("scan.samples"))

    filename = f"{uuid.uuid4().hex}{ext}"
    image_bytes = image_file.read()
    with open(os.path.join(_samples_dir(), filename), "wb") as f:
        f.write(image_bytes)

    ocr_text, ocr_error = _try_ocr(image_bytes)

    extracted_amount = kw_found = extracted_date = None
    next_line = False
    if ocr_text:
        profile = _get_store_profile(store)
        extracted_amount, kw_found, next_line = _parse_amount(
            ocr_text,
            profile["total_keywords"] if profile else None,
            profile["amount_next_line"] if profile else False,
        )
        extracted_date = _parse_date_from_text(ocr_text)

    db.session.add(ReceiptSample(
        store=store,
        image_filename=filename,
        ocr_text=ocr_text,
        notes=notes,
        extracted_amount=extracted_amount,
        extracted_date=extracted_date,
        total_keyword_found=kw_found,
        amount_on_next_line=next_line,
    ))
    db.session.commit()
    _build_store_profile(store)

    if ocr_text:
        amount_str = format_eur(extracted_amount) if extracted_amount else t("samples_no_amount")
        flash(t("flash_sample_saved", amount_str), "success")
    else:
        flash(t("flash_sample_ocr_failed", ocr_error), "warning")
    return redirect(url_for("scan.samples"))


@scan.route("/samples/<int:sample_id>/delete", methods=["POST"])
def delete_sample(sample_id: int):
    """Delete a sample and its image, then rebuild the store's profile."""
    sample = db.get_or_404(ReceiptSample, sample_id)
    store = sample.store
    img_path = os.path.join(_samples_dir(), sample.image_filename)
    if os.path.isfile(img_path):
        os.remove(img_path)
    db.session.delete(sample)
    db.session.commit()
    _build_store_profile(store)
    flash(t("flash_sample_deleted"), "info")
    return redirect(url_for("scan.samples"))


@scan.route("/samples/image/<int:sample_id>")
def sample_image(sample_id: int):
    """Serve a stored sample image."""
    sample = db.get_or_404(ReceiptSample, sample_id)
    img_path = os.path.join(_samples_dir(), sample.image_filename)
    if not os.path.isfile(img_path):
        return "Image not found", 404
    return send_file(img_path)


@scan.route("/samples/<path:store>/reanalyze", methods=["POST"])
def reanalyze_store(store: str):
    """Rebuild one store's profile from its existing samples."""
    _build_store_profile(store)
    n = ReceiptSample.query.filter_by(store=store).filter(ReceiptSample.ocr_text.isnot(None)).count()
    flash(t("flash_profile_rebuilt", store, n), "success")
    return redirect(url_for("scan.samples"))
