"""SendGrid outbound mail and Inbound Parse."""

import json

import pytest
from fastapi.testclient import TestClient

import agencybill.email.sendgrid as sg
from agencybill.email.sendgrid import (
    SendGridError,
    extract_email,
    load_sendgrid_config,
)
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.policy_tools import get_or_create_workflow
from agencybill.web.app import app


class _Response:
    def __init__(self, status=202, message_id="msg-123", body=b""):
        self.status = status
        self.headers = {"X-Message-Id": message_id}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _http_error(code, payload):
    import urllib.error
    body = json.dumps(payload).encode()

    class _Err(urllib.error.HTTPError):
        def read(self, *args, **kwargs):
            return body

    return _Err("https://api.sendgrid.com/v3/mail/send", code, "error", hdrs=None, fp=None)


@pytest.fixture()
def no_sendgrid(monkeypatch):
    for name in (
        "SENDGRID_API_KEY",
        "SENDGRID_FROM_EMAIL",
        "SENDGRID_FROM_NAME",
        "SENDGRID_INBOUND_DOMAIN",
        "SENDGRID_INBOUND_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_without_config_email_is_logged(tmp_db, no_sendgrid):
    result = send_notification("wf-1", "broker@example.com", "Hello", "Body")
    assert result["status"] == "logged"
    assert result["provider"] is None
    assert result["error"] is None


def test_sendgrid_sends_and_sets_reply_to(tmp_db, no_sendgrid, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "renewals@example.com")
    monkeypatch.setenv("SENDGRID_INBOUND_DOMAIN", "parse.example.com")
    captured = {}

    def opener(request, timeout=20):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode())
        return _Response()

    monkeypatch.setattr(sg, "urlopen", opener)
    wf = get_or_create_workflow("pol-1")
    result = send_notification(wf["id"], "Market <mkt@example.com>", "Submission", "Please quote")

    assert result["status"] == "sent"
    assert result["provider"] == "sendgrid"
    assert result["provider_message_id"] == "msg-123"
    assert captured["url"] == "https://api.sendgrid.com/v3/mail/send"
    assert captured["auth"] == "Bearer SG.test-key"
    assert captured["body"]["personalizations"][0]["to"][0]["email"] == "mkt@example.com"
    assert captured["body"]["from"]["email"] == "renewals@example.com"
    assert captured["body"]["reply_to"]["email"] == f"renewals+{wf['id']}@parse.example.com"
    assert captured["body"]["headers"]["X-AgencyBill-Workflow-Id"] == wf["id"]
    assert "SG.test-key" not in json.dumps(result)


def test_sendgrid_failure_is_stored(tmp_db, no_sendgrid, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "renewals@example.com")

    def opener(request, timeout=20):
        raise _http_error(400, {"errors": [{"message": "The from address does not match a verified Sender Identity."}]})

    monkeypatch.setattr(sg, "urlopen", opener)
    result = send_notification("wf-1", "broker@example.com", "Invoice", "Pay this")
    assert result["status"] == "failed"
    assert result["provider"] == "sendgrid"
    assert "verified Sender Identity" in result["error"]
    assert "SG.test-key" not in result["error"]


def test_sms_does_not_call_sendgrid(tmp_db, no_sendgrid, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "renewals@example.com")
    called = {"n": 0}

    def opener(request, timeout=20):
        called["n"] += 1
        return _Response()

    monkeypatch.setattr(sg, "urlopen", opener)
    result = send_notification("wf-1", "555", "Hi", "Body", channel="sms")
    assert result["status"] == "logged"
    assert called["n"] == 0


def test_inbound_links_plus_address_and_rejects_bad_token(tmp_db, no_sendgrid, monkeypatch):
    monkeypatch.setenv("SENDGRID_INBOUND_TOKEN", "inbound-secret")
    wf = get_or_create_workflow("pol-1")
    client = TestClient(app)

    denied = client.post(
        "/webhooks/sendgrid/inbound",
        data={"from": "mkt@example.com", "to": f"renewals+{wf['id']}@parse.example.com", "subject": "Quote", "text": "Here"},
    )
    assert denied.status_code == 401

    ok = client.post(
        "/webhooks/sendgrid/inbound?token=inbound-secret",
        data={
            "from": "Market <mkt@example.com>",
            "to": f"renewals+{wf['id']}@parse.example.com",
            "subject": "Quote attached",
            "text": "Premium is 20000",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["workflow_id"] == wf["id"]
    assert "inbound-secret" not in ok.text

    page = client.get("/mail")
    assert page.status_code == 200
    assert "Quote attached" in page.text
    assert wf["id"][:8] in page.text

    detail = client.get(f"/workflows/{wf['id']}")
    assert "Mail received" in detail.text
    assert "mkt@example.com" in detail.text


def test_inbound_matches_reply_to_previous_recipient(tmp_db, no_sendgrid):
    wf = get_or_create_workflow("pol-1")
    send_notification(wf["id"], "broker@example.com", "Binder", "Attached")
    client = TestClient(app)
    ok = client.post(
        "/webhooks/sendgrid/inbound",
        data={"from": "broker@example.com", "to": "renewals@example.com", "subject": "Re: Binder", "text": "Thanks"},
    )
    assert ok.json()["workflow_id"] == wf["id"]


def test_admin_sendgrid_settings_round_trip(tmp_db, no_sendgrid):
    client = TestClient(app)
    page = client.get("/admin")
    assert 'name="sendgrid_api_key"' in page.text
    assert 'action="/admin/sendgrid"' in page.text

    bad = client.post("/admin/sendgrid", data={
        "sendgrid_from_email": "not-an-email",
        "sendgrid_api_key": "SG.secret-value",
    })
    assert bad.status_code == 400
    assert "SG.secret-value" not in bad.text
    assert load_sendgrid_config().api_key == ""

    saved = client.post("/admin/sendgrid", data={
        "sendgrid_api_key": "SG.secret-value",
        "sendgrid_from_email": "renewals@example.com",
        "sendgrid_from_name": "Agency Bill Desk",
        "sendgrid_inbound_domain": "parse.example.com",
        "sendgrid_inbound_token": "tok-12345678",
    }, follow_redirects=False)
    assert saved.status_code == 303
    config = load_sendgrid_config()
    assert config.can_send
    assert config.api_key == "SG.secret-value"
    assert config.from_email == "renewals@example.com"
    assert config.inbound_domain == "parse.example.com"

    shown = client.get("/admin?notice=sendgrid_saved")
    assert "SG.secret-value" not in shown.text
    assert "tok-12345678" not in shown.text
    assert "renewals@example.com" in shown.text
    assert "SendGrid settings saved" in shown.text

    kept = client.post("/admin/sendgrid", data={
        "sendgrid_from_email": "other@example.com",
    }, follow_redirects=True)
    assert "SG.secret-value" not in kept.text
    assert load_sendgrid_config().api_key == "SG.secret-value"
    assert load_sendgrid_config().from_email == "other@example.com"

    client.post("/admin/sendgrid/clear")
    cleared = load_sendgrid_config()
    assert cleared.api_key == ""
    assert cleared.from_email == ""


def test_extract_email():
    assert extract_email("Market <mkt@example.com>") == "mkt@example.com"
    assert extract_email("") == ""


def test_connection_error(tmp_db, no_sendgrid, monkeypatch):
    import urllib.error
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "renewals@example.com")

    def opener(request, timeout=20):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(sg, "urlopen", opener)
    with pytest.raises(SendGridError, match="connection failed"):
        sg.send_email(to="a@example.com", subject="S", body="B")
