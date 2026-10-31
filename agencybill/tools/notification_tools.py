"""Notification tools. Email is delivered through SendGrid when it is configured."""

import uuid

from agencybill.database import db, row_to_dict
from agencybill.email.sendgrid import (
    SendGridError,
    SendGridNotConfigured,
    send_email,
)


def send_notification(workflow_id: str, recipient: str, subject: str,
                       body: str, channel: str = "email") -> dict:
    """Record a notification and send it when the channel is email and SendGrid is configured.

    Without a SendGrid API key and from-address the row is stored with status
    ``logged``. A SendGrid failure is stored as ``failed`` and returned to the
    agent; the API key is never included in the result.
    """
    status = "logged"
    provider = None
    provider_message_id = None
    error = None
    if (channel or "email").lower() == "email":
        try:
            result = send_email(
                to=recipient,
                subject=subject,
                body=body,
                workflow_id=workflow_id,
            )
            status = "sent"
            provider = "sendgrid"
            provider_message_id = result.get("message_id") or None
        except SendGridNotConfigured:
            status = "logged"
        except SendGridError as exc:
            status = "failed"
            provider = "sendgrid"
            error = str(exc)

    notif_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO notifications
               (id, workflow_id, recipient, subject, body, channel,
                status, provider, provider_message_id, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                notif_id, workflow_id, recipient, subject, body, channel,
                status, provider, provider_message_id, error,
            ),
        )
    return {
        "id": notif_id,
        "workflow_id": workflow_id,
        "recipient": recipient,
        "subject": subject,
        "channel": channel,
        "status": status,
        "provider": provider,
        "provider_message_id": provider_message_id,
        "error": error,
    }


def get_notifications(workflow_id: str) -> list[dict]:
    """Return all notifications sent for a workflow."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE workflow_id = ? ORDER BY sent_at",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]
