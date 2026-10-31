"""SendGrid delivery and Inbound Parse.

Outbound mail uses the v3 Mail Send API when an API key and from-address are
configured. Inbound mail is the multipart POST SendGrid Inbound Parse sends to
``/webhooks/sendgrid/inbound``. Settings saved on the Admin page override the
environment variables of the same name.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from agencybill.database import db, row_to_dict
from agencybill.settings_store import delete_setting, get_setting, set_setting

SENDGRID_API_KEY = "sendgrid_api_key"
SENDGRID_FROM_EMAIL = "sendgrid_from_email"
SENDGRID_FROM_NAME = "sendgrid_from_name"
SENDGRID_INBOUND_DOMAIN = "sendgrid_inbound_domain"
SENDGRID_INBOUND_TOKEN = "sendgrid_inbound_token"

_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
_MAX_BODY = 100_000
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

# Tests replace this with a fake opener. Production uses urllib.
urlopen = urllib.request.urlopen


class SendGridNotConfigured(Exception):
    """API key or from-address is missing, so mail is only logged."""


class SendGridError(Exception):
    """SendGrid rejected or could not accept the message."""


@dataclass(frozen=True)
class SendGridConfig:
    api_key: str
    from_email: str
    from_name: str
    inbound_domain: str
    inbound_token: str

    @property
    def can_send(self) -> bool:
        return bool(self.api_key and self.from_email)


def _pick(setting_key: str, env_name: str, default: str = "") -> str:
    saved = get_setting(setting_key)
    if saved:
        return saved
    return os.getenv(env_name, default).strip()


def load_sendgrid_config() -> SendGridConfig:
    """Effective SendGrid settings. A saved Admin value wins over the environment."""
    return SendGridConfig(
        api_key=_pick(SENDGRID_API_KEY, "SENDGRID_API_KEY"),
        from_email=_pick(SENDGRID_FROM_EMAIL, "SENDGRID_FROM_EMAIL"),
        from_name=_pick(SENDGRID_FROM_NAME, "SENDGRID_FROM_NAME", "AgencyBill") or "AgencyBill",
        inbound_domain=_pick(SENDGRID_INBOUND_DOMAIN, "SENDGRID_INBOUND_DOMAIN").lstrip("@"),
        inbound_token=_pick(SENDGRID_INBOUND_TOKEN, "SENDGRID_INBOUND_TOKEN"),
    )


def save_sendgrid_settings(
    *,
    api_key: str = "",
    from_email: str = "",
    from_name: str = "",
    inbound_domain: str = "",
    inbound_token: str = "",
) -> None:
    """Persist Admin form values. Blank secrets keep the saved secret."""
    from_email = (from_email or "").strip()
    from_name = (from_name or "").strip()
    inbound_domain = (inbound_domain or "").strip().lstrip("@")
    api_key = (api_key or "").strip()
    inbound_token = (inbound_token or "").strip()

    if from_email and not _EMAIL_RE.fullmatch(from_email):
        raise ValueError("From address must be an email")
    if any(ch.isspace() for ch in inbound_domain):
        raise ValueError("Inbound domain cannot contain spaces")
    if "@" in inbound_domain:
        raise ValueError("Inbound domain should not include @")
    if any(ch.isspace() for ch in api_key):
        raise ValueError("API key cannot contain spaces")
    if any(ch.isspace() for ch in inbound_token):
        raise ValueError("Inbound token cannot contain spaces")

    _store_or_clear(SENDGRID_FROM_EMAIL, from_email)
    _store_or_clear(SENDGRID_FROM_NAME, from_name)
    _store_or_clear(SENDGRID_INBOUND_DOMAIN, inbound_domain)
    if api_key:
        set_setting(SENDGRID_API_KEY, api_key)
    if inbound_token:
        set_setting(SENDGRID_INBOUND_TOKEN, inbound_token)


def clear_sendgrid_settings() -> None:
    """Drop every saved SendGrid override so the environment is used again."""
    for key in (
        SENDGRID_API_KEY,
        SENDGRID_FROM_EMAIL,
        SENDGRID_FROM_NAME,
        SENDGRID_INBOUND_DOMAIN,
        SENDGRID_INBOUND_TOKEN,
    ):
        delete_setting(key)


def _store_or_clear(key: str, value: str) -> None:
    if value:
        set_setting(key, value)
    else:
        delete_setting(key)


def reply_to_address(workflow_id: str, config: SendGridConfig | None = None) -> str | None:
    """Plus-address SendGrid Inbound Parse can route back to this workflow."""
    config = config or load_sendgrid_config()
    if not config.inbound_domain or not workflow_id:
        return None
    return f"renewals+{workflow_id}@{config.inbound_domain}"


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    workflow_id: str = "",
) -> dict:
    """POST one plain-text message to SendGrid. Returns the provider message id."""
    config = load_sendgrid_config()
    if not config.can_send:
        raise SendGridNotConfigured("SendGrid API key and from address are required")
    recipient = extract_email(to) or (to or "").strip()
    if not recipient:
        raise SendGridError("Recipient email is missing")

    payload: dict = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": config.from_email, "name": config.from_name or "AgencyBill"},
        "subject": subject or "(no subject)",
        "content": [{"type": "text/plain", "value": body or ""}],
    }
    reply_to = reply_to_address(workflow_id, config)
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    if workflow_id:
        payload["headers"] = {"X-AgencyBill-Workflow-Id": workflow_id}

    request = urllib.request.Request(
        _MAIL_SEND_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            message_id = response.headers.get("X-Message-Id", "")
            return {"message_id": message_id, "status_code": getattr(response, "status", 202)}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        message = _error_message(detail) or f"SendGrid HTTP {exc.code}"
        raise SendGridError(message) from exc
    except urllib.error.URLError as exc:
        raise SendGridError(f"SendGrid connection failed: {exc.reason}") from exc


def _error_message(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return ""
    errors = parsed.get("errors") if isinstance(parsed, dict) else None
    if isinstance(errors, list) and errors:
        message = errors[0].get("message") if isinstance(errors[0], dict) else ""
        return str(message or "")
    return ""


def extract_email(value: str) -> str:
    match = _EMAIL_RE.search(value or "")
    return match.group(0).lower() if match else ""


def store_inbound(form) -> dict:
    """Persist one Inbound Parse submission and link it to a workflow when possible."""
    from_address = _field(form, "from")
    to_address = _field(form, "to")
    subject = _field(form, "subject")
    text_body = _field(form, "text")[:_MAX_BODY]
    html_body = _field(form, "html")[:_MAX_BODY]
    headers = _field(form, "headers")[:_MAX_BODY]
    workflow_id = match_workflow(
        to_address=to_address,
        subject=subject,
        text_body=text_body,
        headers=headers,
        from_address=from_address,
    )
    email_id = str(uuid.uuid4())
    attachment_count = _attachment_count(form)
    with db() as conn:
        conn.execute(
            """INSERT INTO inbound_emails
               (id, workflow_id, from_address, to_address, subject,
                text_body, html_body, message_headers, attachment_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                email_id, workflow_id, from_address, to_address, subject,
                text_body, html_body, headers, attachment_count,
            ),
        )
    return {
        "id": email_id,
        "workflow_id": workflow_id,
        "from_address": from_address,
        "to_address": to_address,
        "subject": subject,
        "status": "received",
    }


def list_inbound_emails(workflow_id: str | None = None, limit: int = 100) -> list[dict]:
    """Newest inbound messages, optionally for one workflow."""
    limit = max(1, min(int(limit), 200))
    with db() as conn:
        if workflow_id:
            rows = conn.execute(
                """SELECT * FROM inbound_emails
                   WHERE workflow_id = ?
                   ORDER BY received_at DESC
                   LIMIT ?""",
                (workflow_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM inbound_emails
                   ORDER BY received_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    return [row_to_dict(row) for row in rows]


def match_workflow(
    *,
    to_address: str,
    subject: str,
    text_body: str,
    headers: str,
    from_address: str,
) -> str | None:
    """Prefer a workflow id in the recipient address, then a reply to someone we emailed."""
    blob = "\n".join((to_address, subject, headers, text_body[:2000]))
    for candidate in _UUID_RE.findall(blob):
        if _workflow_exists(candidate):
            return candidate
    sender = extract_email(from_address)
    if not sender:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT workflow_id, recipient FROM notifications
               WHERE workflow_id IS NOT NULL
               ORDER BY sent_at DESC
               LIMIT 50"""
        ).fetchall()
    for item in row:
        if extract_email(item["recipient"] or "") == sender and item["workflow_id"]:
            if _workflow_exists(item["workflow_id"]):
                return item["workflow_id"]
    return None


def _workflow_exists(workflow_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM renewal_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
    return row is not None


def _field(form, key: str) -> str:
    value = form.get(key) if hasattr(form, "get") else None
    if value is None or hasattr(value, "read"):
        return ""
    return str(value)


def _attachment_count(form) -> int:
    count = 0
    try:
        items = form.items()
    except Exception:
        return 0
    for key, value in items:
        if hasattr(value, "read") or str(key).lower().startswith("attachment"):
            if hasattr(value, "read"):
                count += 1
    return count
