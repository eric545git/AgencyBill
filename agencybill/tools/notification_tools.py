"""Simulated notification tools (email/SMS) — logs to DB rather than sending real mail."""

import uuid
import json

from agencybill.database import db


def send_notification(workflow_id: str, recipient: str, subject: str,
                       body: str, channel: str = "email") -> dict:
    """Log a notification (simulated send). Returns notification record."""
    notif_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO notifications (id, workflow_id, recipient, subject, body, channel)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (notif_id, workflow_id, recipient, subject, body, channel),
        )
    return {
        "id": notif_id,
        "workflow_id": workflow_id,
        "recipient": recipient,
        "subject": subject,
        "channel": channel,
        "status": "sent",
    }


def get_notifications(workflow_id: str) -> list[dict]:
    """Return all notifications sent for a workflow."""
    from agencybill.database import row_to_dict
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE workflow_id = ? ORDER BY sent_at",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]
