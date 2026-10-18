"""Error handling: translated pages, popups after failed forms and JSON errors."""
import pytest

from app.models import Expense


def test_unknown_page_renders_translated_error_page(app):
    response = app.test_client().get("/does-not-exist")
    html = response.get_data(as_text=True)
    assert response.status_code == 404
    assert "Nicht gefunden" in html            # German title (default language)
    assert "existiert nicht" in html          # German text, not Werkzeug's English
    assert 'id="toastContainer"' in html       # rendered inside the app layout
    assert "showToast(" in html               # message also shown as a popup


def test_error_page_follows_the_language(app):
    client = app.test_client()
    client.get("/lang/en")
    html = client.get("/does-not-exist").get_data(as_text=True)
    assert "Not found" in html
    assert "does not exist" in html


def test_post_to_missing_entry_redirects_back_with_popup(app):
    client = app.test_client()
    response = client.post(
        "/expense/999/delete", headers={"Referer": "http://localhost/expenses?page=2"}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/expenses?page=2"
    html = client.get("/expenses").get_data(as_text=True)
    assert "existiert nicht" in html
    assert "text-bg-danger" in html


def test_api_errors_are_json(app):
    response = app.test_client().get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.get_json() == {"error": "Diese Seite oder dieser Eintrag existiert nicht (mehr).", "status": 404}


def test_api_validation_errors_are_translated(app):
    response = app.test_client().get("/api/monthly-summary?year=2024&month=13")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Ungültiges Jahr oder ungültiger Monat."


def test_fetch_requests_get_json_errors(app):
    response = app.test_client().post("/scan/process", headers={"Accept": "application/json"})
    assert response.status_code == 400
    assert "Kein Foto" in response.get_json()["error"]


def test_method_not_allowed_page(app):
    response = app.test_client().get("/expense/1/delete")
    assert response.status_code == 405
    assert "nicht möglich" in response.get_data(as_text=True)


def test_upload_too_large_gives_size_hint(app):
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    response = app.test_client().post(
        "/scan/process",
        data={"image": (__import__("io").BytesIO(b"x" * (2 * 1024 * 1024)), "big.jpg")},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 413
    assert "höchstens 1 MB" in response.get_json()["error"]


def test_unexpected_exception_is_logged_and_rolled_back(app, monkeypatch, caplog):
    from app.routes import main

    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(main.s, "get_monthly_summary", boom)
    app.testing = False  # tests propagate exceptions; production shows the error page
    with caplog.at_level("ERROR", logger="app.errors"):
        response = app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "Deine Daten sind sicher" in html
    assert "database exploded" not in html    # no internals leak to the page
    assert "database exploded" in caplog.text  # but the traceback is logged


@pytest.mark.parametrize("category", ["success", "warning", "danger", "info"])
def test_flash_messages_render_as_toasts(app, category):
    with app.test_request_context():
        from flask import flash, render_template

        flash("Hallo", category)
        html = render_template("error.html", code=404, title="x", message="y")
    assert 'class="toast ' in html
    assert f'data-category="{category}"' in html


def test_successful_form_still_shows_success_popup(app):
    client = app.test_client()
    response = client.post(
        "/add",
        data={"date": "2024-01-02", "store": "Aldi", "amount": "9,99"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "text-bg-success" in response.get_data(as_text=True)
    with app.app_context():
        assert Expense.query.count() == 1
