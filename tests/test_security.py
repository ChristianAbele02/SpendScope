"""Host check, LAN pairing and CSRF protection (app.security)."""
import re

import pytest

import app as app_module
from app import _load_secret_key
from app.models import StoreAlias

LAN_CLIENT = {"REMOTE_ADDR": "192.168.1.50"}
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def csrf_app(app):
    """The test app with the CSRF check switched on, as in production."""
    app.config["CSRF_ENABLED"] = True
    return app


def _token(client) -> str:
    match = CSRF_RE.search(client.get("/settings/store-aliases").get_data(as_text=True))
    assert match, "page should render a CSRF field"
    return match.group(1)


def _alias_form(**extra: str) -> dict[str, str]:
    return {"alias": "REWE Markt", "canonical": "Rewe", **extra}


# ── CSRF ──────────────────────────────────────────────────────────────────────

def test_post_without_csrf_token_is_rejected(csrf_app):
    client = csrf_app.test_client()
    assert client.post("/settings/store-aliases/add", data=_alias_form()).status_code == 400
    with csrf_app.app_context():
        assert StoreAlias.query.count() == 0


def test_post_with_wrong_csrf_token_is_rejected(csrf_app):
    client = csrf_app.test_client()
    _token(client)
    response = client.post("/settings/store-aliases/add", data=_alias_form(csrf_token="forged"))
    assert response.status_code == 400


def test_post_with_form_token_is_accepted(csrf_app):
    client = csrf_app.test_client()
    token = _token(client)
    response = client.post("/settings/store-aliases/add", data=_alias_form(csrf_token=token))
    assert response.status_code == 302
    with csrf_app.app_context():
        assert StoreAlias.query.count() == 1


def test_post_with_header_token_is_accepted(csrf_app):
    client = csrf_app.test_client()
    token = _token(client)
    response = client.post(
        "/settings/store-aliases/add", data=_alias_form(), headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 302


def test_token_from_another_session_is_rejected(csrf_app):
    token = _token(csrf_app.test_client())
    other = csrf_app.test_client()
    _token(other)
    response = other.post("/settings/store-aliases/add", data=_alias_form(csrf_token=token))
    assert response.status_code == 400


def test_every_post_form_carries_a_csrf_field(app):
    from pathlib import Path

    templates = Path(app.root_path) / "templates"
    form_re = re.compile(r'<form\b[^>]*method="post"[^>]*>', re.IGNORECASE)
    for path in templates.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        for match in form_re.finditer(html):
            following = html[match.end() : match.end() + 120]
            assert "csrf_token()" in following, f"{path.name}: {match.group(0)[:60]}"


# ── LAN pairing ───────────────────────────────────────────────────────────────

def test_loopback_needs_no_pairing(app):
    assert app.test_client().get("/").status_code == 200


def test_unpaired_lan_device_is_rejected(app):
    client = app.test_client()
    assert client.get("/", environ_base=LAN_CLIENT).status_code == 403
    assert client.get("/scan/", environ_base=LAN_CLIENT).status_code == 403


def test_wrong_pairing_token_is_rejected(app):
    client = app.test_client()
    assert client.get("/scan/?pair=guess", environ_base=LAN_CLIENT).status_code == 403


def test_pairing_token_pairs_the_device(app):
    client = app.test_client()
    token = app.config["LAN_TOKEN"]
    assert client.get(f"/scan/?pair={token}", environ_base=LAN_CLIENT).status_code == 200
    # The session remembers the pairing; later requests need no token.
    assert client.get("/", environ_base=LAN_CLIENT).status_code == 200


def test_pairing_expires_when_the_token_changes(app):
    client = app.test_client()
    client.get(f"/scan/?pair={app.config['LAN_TOKEN']}", environ_base=LAN_CLIENT)
    app.config["LAN_TOKEN"] = "new-token-after-restart"
    assert client.get("/", environ_base=LAN_CLIENT).status_code == 403


def test_qr_page_url_contains_pairing_token(app):
    html = app.test_client().get("/scan/qr").get_data(as_text=True)
    assert f"pair={app.config['LAN_TOKEN']}" in html


def test_local_mode_hides_qr_code(app):
    app.config["LAN_ACCESS"] = False
    client = app.test_client()
    for page in ("/add", "/scan/qr"):
        html = client.get(page).get_data(as_text=True)
        assert "/scan/qr-image" not in html
        assert "--local" in html


# ── Host header ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["localhost:5000", "127.0.0.1:5000", "192.168.1.5:5000", "[::1]:5000"])
def test_ip_and_localhost_hosts_are_accepted(app, host):
    assert app.test_client().get("/", headers={"Host": host}).status_code == 200


def test_foreign_hostname_is_rejected(app):
    # DNS rebinding: a website's own domain resolving to 127.0.0.1.
    response = app.test_client().get("/", headers={"Host": "attacker.example:5000"})
    assert response.status_code == 400


def test_allowed_hosts_extends_the_list(app):
    app.config["ALLOWED_HOSTS"] = ["spendscope.lan"]
    response = app.test_client().get("/", headers={"Host": "spendscope.lan:5000"})
    assert response.status_code == 200


# ── Session key ───────────────────────────────────────────────────────────────

def test_secret_key_is_generated_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    first = _load_secret_key(testing=False)
    assert len(first) >= 32
    assert (tmp_path / "secret_key").read_text(encoding="ascii") == first
    assert _load_secret_key(testing=False) == first


def test_testing_never_writes_a_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    assert _load_secret_key(testing=True) != _load_secret_key(testing=True)
    assert not (tmp_path / "secret_key").exists()
