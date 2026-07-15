"""
Receipt scanning via QR code.

Flow:
  1. Desktop shows /scan/qr  → QR code encodes http://<host>/scan
  2. Phone opens /scan        → mobile camera-capture page
  3. Phone POSTs image to /scan/process → JSON with extracted fields
  4. Phone submits confirmed form to /scan/save → expense saved
"""
import io
import os
import re
import socket
import uuid as _uuid
from datetime import UTC, date, datetime

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app import db
from app.models import Expense
from app.parser import get_category

scan = Blueprint("scan", __name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Best-effort detection of the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Known store names to match against OCR text (ordered longest-first to avoid
# "DM" matching inside "Marktkauf" etc.)
_KNOWN_STORES = sorted([
    "Müller", "Mueller", "Aldi", "Lidl", "Rewe", "Penny", "Kaufland",
    "Edeka", "Marktkauf", "Combi", "Netto", "Rossmann", "DM", "Action",
    "Amazon", "Ikea", "Jysk", "OBI", "Toom", "Waschstraße", "Subway",
], key=len, reverse=True)


def _try_ocr(image_bytes: bytes) -> tuple[str | None, str | None]:
    """
    Run Tesseract OCR on the image.
    Returns (text, error_message). On success error_message is None.
    """
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as e:
        return None, f"Missing Python package: {e}. Run: pip install pytesseract Pillow"

    # Guard against decompression-bomb images: refuse anything over ~50 MP.
    # A phone photo is well under this; the request body itself is also capped
    # by MAX_CONTENT_LENGTH.
    Image.MAX_IMAGE_PIXELS = 50_000_000

    try:
        from config import Config
        if Config.TESSERACT_CMD and os.path.isfile(Config.TESSERACT_CMD):
            pytesseract.pytesseract.tesseract_cmd = Config.TESSERACT_CMD

        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # force decode now so an oversized image fails here, before OCR

        # Rotate according to EXIF orientation (phones often shoot sideways)
        img = ImageOps.exif_transpose(img)

        # Convert to grayscale
        img = img.convert("L")

        # Resize so the shorter edge ≤ 2000 px — Tesseract doesn't need huge images
        # and smaller means faster + fewer OCR artifacts
        max_dim = 2000
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Mild contrast boost then sharpen
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = img.filter(ImageFilter.SHARPEN)

        # Simple binarisation: everything below mid-grey → black, above → white
        # Helps enormously on receipt paper which is off-white
        img = img.point(lambda p: 0 if p < 160 else 255)

        # PSM 4 = single column of variable-size text — ideal for narrow receipts
        cfg = "--psm 4 --oem 3"
        try:
            text = pytesseract.image_to_string(img, lang="deu+eng", config=cfg)
        except pytesseract.pytesseract.TesseractError:
            text = pytesseract.image_to_string(img, lang="eng", config=cfg)

        return text, None
    except Exception as e:
        return None, str(e)


_CANDIDATE_KEYWORDS = [
    "GESAMTBETRAG", "ENDBETRAG", "ZU ZAHLEN", "SUMME EUR",
    "GESAMT EUR", "GESAMT", "SUMME", "TOTAL EUR", "TOTAL",
    "BARGELD", "BAR EUR", "BAR",
]

_GENERIC_KW_RE = re.compile(
    r"(?:GES(?:AMT)?(?:BETRAG)?|SUMME|ZU\s*ZAHLEN|TOTAL|ENDBETRAG|BARGELD|BAR(?:\s*EUR)?)",
    re.IGNORECASE,
)


def _parse_amount(
    text: str,
    preferred_keywords: list | None = None,
    prefer_next_line: bool = False,
) -> tuple[float | None, str | None, bool]:
    """
    Extract the total amount from receipt text.
    Returns (amount, keyword_used, was_on_next_line).
    preferred_keywords (from a StoreProfile) are tried before generic patterns.
    """
    amount_re = re.compile(r"(\d{1,4}[,\.]\d{2})")
    lines = text.splitlines()

    def _scan(pattern: re.Pattern, prefer_next: bool):
        for i, line in enumerate(lines):
            if not pattern.search(line):
                continue
            if not prefer_next:
                m = amount_re.search(line)
                if m:
                    try:
                        return round(float(m.group(1).replace(",", ".")), 2), False
                    except ValueError:
                        pass
            if i + 1 < len(lines):
                m = amount_re.search(lines[i + 1])
                if m:
                    try:
                        return round(float(m.group(1).replace(",", ".")), 2), True
                    except ValueError:
                        pass
            # same-line fallback when prefer_next
            m = amount_re.search(line)
            if m:
                try:
                    return round(float(m.group(1).replace(",", ".")), 2), False
                except ValueError:
                    pass
        return None, False

    # 1. Profile keywords first
    if preferred_keywords:
        for kw in preferred_keywords:
            amount, next_line = _scan(re.compile(re.escape(kw), re.IGNORECASE), prefer_next_line)
            if amount is not None:
                return amount, kw, next_line

    # 2. Generic keyword scan
    amount, next_line = _scan(_GENERIC_KW_RE, prefer_next_line)
    if amount is not None:
        # Find which keyword matched for reporting
        for line in lines:
            m = _GENERIC_KW_RE.search(line)
            if m:
                return amount, m.group(0).upper(), next_line
        return amount, None, next_line

    # 3. Largest plausible amount in document
    candidates = []
    for raw in amount_re.findall(text):
        try:
            v = float(raw.replace(",", "."))
            if 0.5 <= v <= 9999:
                candidates.append(v)
        except ValueError:
            pass
    if candidates:
        return round(max(candidates), 2), None, False
    return None, None, False


def _parse_receipt(text: str) -> dict:
    """
    Extract store, amount, and date from raw OCR text of a German receipt.
    All fields are best-effort; missing ones are returned as None.
    """
    result: dict = {
        "store": None, "amount": None, "date": None, "raw": text,
        "keyword_found": None, "store_known": False,
    }
    if not text:
        return result

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return result

    # ── Store name ────────────────────────────────────────────────────────
    # 1) Scan first 25 lines for a known store name (case-insensitive)
    for line in lines[:25]:
        for store in _KNOWN_STORES:
            if store.lower() in line.lower():
                result["store"] = store  # use canonical capitalisation
                result["store_known"] = True
                break
        if result["store"]:
            break

    # 2) Fallback: first substantial non-numeric line
    if not result["store"]:
        for line in lines[:8]:
            if len(line) >= 3 and not re.match(r"^[\d\s\.\,\-\+\*\/\(\)€]+$", line):
                result["store"] = line[:60]
                break

    # ── Total amount — use store profile if available ─────────────────────
    profile = _get_store_profile(result["store"]) if result["store"] else None
    pref_kws   = profile["total_keywords"]  if profile else None
    pref_next  = profile["amount_next_line"] if profile else False
    amount, kw_found, _ = _parse_amount(text, pref_kws, pref_next)
    result["amount"]        = amount
    result["keyword_found"] = kw_found

    # ── Date ──────────────────────────────────────────────────────────────
    today = date.today()
    date_candidates: list[date] = []

    # DD.MM.YYYY  /  DD/MM/YYYY  /  DD-MM-YYYY
    for m in re.finditer(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b", text):
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if d <= today and (today - d).days <= 90:
                date_candidates.append(d)
        except ValueError:
            pass

    # DD.MM.YY
    for m in re.finditer(r"\b(\d{2})[./\-](\d{2})[./\-](\d{2})\b", text):
        try:
            year = 2000 + int(m.group(3))
            d = date(year, int(m.group(2)), int(m.group(1)))
            if d <= today and (today - d).days <= 90:
                date_candidates.append(d)
        except ValueError:
            pass

    if date_candidates:
        # Prefer the most recent plausible date (within the last 90 days)
        result["date"] = max(date_candidates).isoformat()

    return result


# Heuristic confidences for the Tesseract fallback. The Claude backend reports
# its own; these approximate how reliable each Tesseract guess historically is.
_CONF_STORE_KNOWN = 0.8      # store matched against _KNOWN_STORES
_CONF_STORE_GUESS = 0.3      # first substantial text line fallback
_CONF_AMOUNT_KEYWORD = 0.7   # amount found next to a total keyword
_CONF_AMOUNT_FALLBACK = 0.4  # largest plausible amount in the document
_CONF_DATE_FOUND = 0.7
_MAX_AMOUNT_CANDIDATES = 3


def _tesseract_fields(parsed: dict, text: str) -> dict:
    """Map a ``_parse_receipt`` result onto the unified extraction shape.

    Attaches heuristic per-field confidences and up to
    ``_MAX_AMOUNT_CANDIDATES`` alternative amount candidates (distinct
    plausible values found anywhere in the OCR text, largest first) so the
    mobile form can offer them when the primary guess is wrong.
    """
    from app.extraction import empty_result

    result = empty_result("tesseract")
    result["store"] = parsed.get("store")
    result["amount"] = parsed.get("amount")
    result["date"] = parsed.get("date")
    result["raw"] = parsed.get("raw")

    if result["store"]:
        result["fields"]["store"]["confidence"] = (
            _CONF_STORE_KNOWN if parsed.get("store_known") else _CONF_STORE_GUESS
        )
    if result["amount"] is not None:
        result["fields"]["amount"]["confidence"] = (
            _CONF_AMOUNT_KEYWORD if parsed.get("keyword_found") else _CONF_AMOUNT_FALLBACK
        )
    if result["date"]:
        result["fields"]["date"]["confidence"] = _CONF_DATE_FOUND

    amount_re = re.compile(r"(\d{1,4}[,\.]\d{2})")
    seen: set[float] = set()
    for raw_value in amount_re.findall(text or ""):
        try:
            v = round(float(raw_value.replace(",", ".")), 2)
        except ValueError:
            continue
        if 0.5 <= v <= 9999 and v != result["amount"]:
            seen.add(v)
    result["fields"]["amount"]["candidates"] = sorted(seen, reverse=True)[:_MAX_AMOUNT_CANDIDATES]

    return result


# ── Routes ───────────────────────────────────────────────────────────────────

@scan.route("/")
def mobile():
    """Mobile receipt capture page."""
    return render_template("scan_mobile.html")


@scan.route("/qr")
def qr_page():
    """Desktop page that shows the QR code for the scan URL."""
    port = request.environ.get("SERVER_PORT", 5000)
    scan_url = f"http://{_local_ip()}:{port}/scan/"
    return render_template("scan_qr.html", scan_url=scan_url)


@scan.route("/qr-image")
def qr_image():
    """Return a QR code PNG for the scan URL (server-side generated, always correct LAN IP)."""
    import qrcode

    port = request.environ.get("SERVER_PORT", 5000)
    scan_url = f"http://{_local_ip()}:{port}/scan/"

    qr = qrcode.QRCode(box_size=10, border=3)
    qr.add_data(scan_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.read(), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@scan.route("/process", methods=["POST"])
def process():
    """Extract expense fields from an uploaded receipt image.

    Tries the Claude vision backend first (when configured), then falls back
    to local Tesseract OCR. Returns JSON in the unified extraction shape:
    ``{store, amount, date, raw, backend, fields: {store|amount|date:
    {confidence, candidates}}, ocr_available, ocr_error, llm_error}``.
    """
    from app import extraction

    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    image_bytes = request.files["image"].read()

    llm_error = None
    if extraction.claude_configured():
        result, llm_error = extraction.extract_with_claude(image_bytes)
        if result is not None:
            result["ocr_available"] = True
            result["ocr_error"] = None
            result["llm_error"] = None
            return jsonify(result)

    ocr_text, ocr_error = _try_ocr(image_bytes)
    ocr_available = ocr_text is not None

    if ocr_available:
        result = _tesseract_fields(_parse_receipt(ocr_text), ocr_text)
    else:
        result = extraction.empty_result("tesseract")

    result["ocr_available"] = ocr_available
    result["ocr_error"] = ocr_error
    result["llm_error"] = llm_error
    return jsonify(result)


@scan.route("/save", methods=["POST"])
def save():
    """Save the confirmed expense from the scan form."""
    date_str   = request.form.get("date", "").strip()
    store      = request.form.get("store", "").strip()
    amount_str = request.form.get("amount", "").strip()
    notes      = request.form.get("notes", "").strip() or None

    errors = []
    if not store:
        errors.append("Store required.")

    try:
        amount = round(float(amount_str.replace(",", ".")), 2)
    except ValueError:
        errors.append("Invalid amount.")
        amount = None

    date_val = None
    if date_str:
        try:
            date_val = date.fromisoformat(date_str)
        except ValueError:
            errors.append("Invalid date.")

    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("scan.mobile"))

    expense = Expense(
        date=date_val,
        store=store,
        amount=amount,
        category=get_category(store),
        notes=notes,
    )
    db.session.add(expense)
    db.session.commit()
    flash(f"Expense of €{amount:.2f} at {store} saved.", "success")
    return redirect(url_for("main.dashboard"))


# ── Receipt sample database ───────────────────────────────────────────────────


def _samples_dir() -> str:
    """Return (and create) the directory where sample images are stored."""
    from config import Config
    d = os.path.join(os.path.dirname(Config.CSV_PATH), "data", "receipt_samples")
    os.makedirs(d, exist_ok=True)
    return d


def _build_store_profile(store: str) -> None:
    """
    Analyse all OCR-processed samples for *store* and update (or delete) its
    StoreProfile, learning which total keyword and line layout works best.
    """
    import json

    from app.models import ReceiptSample, StoreProfile

    samples = (ReceiptSample.query
               .filter_by(store=store)
               .filter(ReceiptSample.ocr_text.isnot(None))
               .all())

    if not samples:
        existing = StoreProfile.query.filter_by(store=store).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
        return

    amount_re = re.compile(r"(\d{1,4}[,\.]\d{2})")
    kw_hits: dict[str, dict] = {kw: {"total": 0, "next_line": 0} for kw in _CANDIDATE_KEYWORDS}

    for sample in samples:
        lines = sample.ocr_text.splitlines()
        for kw in _CANDIDATE_KEYWORDS:
            kw_re = re.compile(re.escape(kw), re.IGNORECASE)
            for i, line in enumerate(lines):
                if not kw_re.search(line):
                    continue
                if amount_re.search(line):
                    kw_hits[kw]["total"] += 1
                    break
                if i + 1 < len(lines) and amount_re.search(lines[i + 1]):
                    kw_hits[kw]["total"]     += 1
                    kw_hits[kw]["next_line"] += 1
                    break

    successful = sorted(
        [(kw, d) for kw, d in kw_hits.items() if d["total"] > 0],
        key=lambda x: x[1]["total"], reverse=True,
    )
    ordered_kws = [kw for kw, _ in successful]
    total_votes     = sum(d["total"]     for _, d in successful)
    next_line_votes = sum(d["next_line"] for _, d in successful)
    amount_next_line = bool(total_votes and next_line_votes / total_votes > 0.5)

    profile = StoreProfile.query.filter_by(store=store).first()
    if not profile:
        profile = StoreProfile(store=store)
        db.session.add(profile)

    profile.total_keywords_json = json.dumps(ordered_kws)
    profile.amount_next_line    = amount_next_line
    profile.sample_count        = len(samples)
    profile.last_updated        = datetime.now(UTC)
    db.session.commit()


def _get_store_profile(store: str) -> dict | None:
    """Load the learned profile for *store*, or None if none exists yet."""
    from app.models import StoreProfile
    if not store:
        return None
    p = StoreProfile.query.filter_by(store=store).first()
    if not p or not p.total_keywords:
        return None
    return {"total_keywords": p.total_keywords, "amount_next_line": p.amount_next_line}


@scan.route("/samples")
def samples():
    from app.models import ReceiptSample, StoreProfile
    all_samples = ReceiptSample.query.order_by(ReceiptSample.uploaded_at.desc()).all()
    profiles    = StoreProfile.query.order_by(StoreProfile.store).all()
    all_stores  = sorted({s.store for s in all_samples})
    return render_template("scan_samples.html",
                           samples=all_samples,
                           profiles=profiles,
                           all_stores=all_stores)


@scan.route("/samples/upload", methods=["POST"])
def upload_sample():
    from app.models import ReceiptSample
    store      = request.form.get("store", "").strip()
    notes      = request.form.get("notes", "").strip() or None
    image_file = request.files.get("image")

    if not store or not image_file or not image_file.filename:
        flash("Store and image are required.", "danger")
        return redirect(url_for("scan.samples"))

    ext      = os.path.splitext(image_file.filename)[1].lower() or ".jpg"
    filename = f"{_uuid.uuid4().hex}{ext}"
    image_bytes = image_file.read()
    with open(os.path.join(_samples_dir(), filename), "wb") as f:
        f.write(image_bytes)

    ocr_text, ocr_error = _try_ocr(image_bytes)

    extracted_amount = extracted_date = kw_found = None
    next_line = False

    if ocr_text:
        profile  = _get_store_profile(store)
        pref_kws = profile["total_keywords"]  if profile else None
        pref_nxt = profile["amount_next_line"] if profile else False
        extracted_amount, kw_found, next_line = _parse_amount(ocr_text, pref_kws, pref_nxt)

        parsed = _parse_receipt(ocr_text)
        if parsed.get("date"):
            try:
                extracted_date = date.fromisoformat(parsed["date"])
            except ValueError:
                pass

    sample = ReceiptSample(
        store=store,
        image_filename=filename,
        ocr_text=ocr_text,
        notes=notes,
        extracted_amount=extracted_amount,
        extracted_date=extracted_date,
        total_keyword_found=kw_found,
        amount_on_next_line=next_line,
    )
    db.session.add(sample)
    db.session.commit()

    _build_store_profile(store)

    if ocr_text:
        amt_str = f"€{extracted_amount:.2f}" if extracted_amount else "not detected"
        flash(f"Sample saved and analysed — amount: {amt_str}.", "success")
    else:
        flash(f"Sample saved, but OCR failed: {ocr_error}", "warning")

    return redirect(url_for("scan.samples"))


@scan.route("/samples/<int:id>/delete", methods=["POST"])
def delete_sample(id):
    from app.models import ReceiptSample
    sample = ReceiptSample.query.get_or_404(id)
    store  = sample.store
    img_path = os.path.join(_samples_dir(), sample.image_filename)
    if os.path.isfile(img_path):
        os.remove(img_path)
    db.session.delete(sample)
    db.session.commit()
    _build_store_profile(store)
    flash("Sample deleted.", "info")
    return redirect(url_for("scan.samples"))


@scan.route("/samples/image/<int:id>")
def sample_image(id):
    from app.models import ReceiptSample
    sample   = ReceiptSample.query.get_or_404(id)
    img_path = os.path.join(_samples_dir(), sample.image_filename)
    if not os.path.isfile(img_path):
        return "Image not found", 404
    return send_file(img_path)


@scan.route("/samples/<path:store>/reanalyze", methods=["POST"])
def reanalyze_store(store):
    from app.models import ReceiptSample
    _build_store_profile(store)
    n = ReceiptSample.query.filter_by(store=store).filter(
        ReceiptSample.ocr_text.isnot(None)).count()
    flash(f"Profile for '{store}' rebuilt from {n} samples.", "success")
    return redirect(url_for("scan.samples"))
