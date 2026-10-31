"""Shared helpers for renewal applications."""

from agencybill.database import db, row_to_dict


def get_application(workflow_id: str) -> dict:
    """Return the latest application for a workflow, or empty dict."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)
