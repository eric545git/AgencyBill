"""Tools for reading and writing policy and workflow data."""

import uuid
from datetime import datetime, date, timedelta
from typing import Any

from agencybill.database import db, row_to_dict


def list_expiring_policies(days: int = 90) -> list[dict]:
    """Return all active policies expiring within `days` days."""
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT p.*, f.name AS firm_name, f.state AS firm_state,
                   f.email AS firm_email, b.name AS broker_name, b.email AS broker_email
            FROM policies p
            JOIN law_firms f ON p.firm_id = f.id
            LEFT JOIN brokers b ON p.broker_id = b.id
            WHERE p.status = 'active'
              AND p.expiration_date >= ?
              AND p.expiration_date <= ?
            ORDER BY p.expiration_date
            """,
            (today, cutoff),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def read_policy(policy_id: str) -> dict:
    """Return full policy record including firm and broker info."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT p.*, f.name AS firm_name, f.state AS firm_state,
                   f.city AS firm_city, f.email AS firm_email,
                   f.phone AS firm_phone, f.firm_type,
                   b.name AS broker_name, b.email AS broker_email
            FROM policies p
            JOIN law_firms f ON p.firm_id = f.id
            LEFT JOIN brokers b ON p.broker_id = b.id
            WHERE p.id = ?
            """,
            (policy_id,),
        ).fetchone()
    return row_to_dict(row)


def update_policy_status(policy_id: str, status: str) -> None:
    """Update the status of a policy (active/expired/cancelled/non-renewed)."""
    with db() as conn:
        conn.execute(
            "UPDATE policies SET status = ? WHERE id = ?",
            (status, policy_id),
        )


def get_or_create_workflow(policy_id: str) -> dict:
    """Return an existing active workflow for a policy, or create one."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM renewal_workflows WHERE policy_id = ? AND status = 'active' ORDER BY started_at DESC LIMIT 1",
            (policy_id,),
        ).fetchone()
        if row:
            return row_to_dict(row)
        # Create new
        wf_id = str(uuid.uuid4())
        year = datetime.now().year + 1
        conn.execute(
            """INSERT INTO renewal_workflows (id, policy_id, phase, status, renewal_year)
               VALUES (?, ?, 'PRE_RENEWAL', 'active', ?)""",
            (wf_id, policy_id, year),
        )
        return {"id": wf_id, "policy_id": policy_id, "phase": "PRE_RENEWAL",
                "status": "active", "renewal_year": year}


def read_workflow(workflow_id: str) -> dict:
    """Return a renewal workflow by ID."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM renewal_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


def update_workflow_phase(workflow_id: str, phase: str) -> None:
    """Advance the workflow to the next phase."""
    with db() as conn:
        conn.execute(
            "UPDATE renewal_workflows SET phase = ?, updated_at = datetime('now') WHERE id = ?",
            (phase, workflow_id),
        )


def update_workflow_status(workflow_id: str, status: str, notes: str = "") -> None:
    """Update workflow status (active/paused/complete/non_renewal/declined/cancelled)."""
    with db() as conn:
        conn.execute(
            """UPDATE renewal_workflows
               SET status = ?, notes = COALESCE(NULLIF(?, ''), notes),
                   updated_at = datetime('now'),
                   completed_at = CASE WHEN ? IN ('complete','non_renewal','declined') THEN datetime('now') ELSE completed_at END
               WHERE id = ?""",
            (status, notes, status, workflow_id),
        )


def list_workflows(status: str = None) -> list[dict]:
    """Return all workflows, optionally filtered by status."""
    with db() as conn:
        if status:
            rows = conn.execute(
                """SELECT w.*, p.policy_number, p.expiration_date,
                          f.name AS firm_name, f.state AS firm_state
                   FROM renewal_workflows w
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE w.status = ?
                   ORDER BY w.updated_at DESC""",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT w.*, p.policy_number, p.expiration_date,
                          f.name AS firm_name, f.state AS firm_state
                   FROM renewal_workflows w
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   ORDER BY w.updated_at DESC""",
            ).fetchall()
    return [row_to_dict(r) for r in rows]


def log_workflow_event(workflow_id: str, phase: str, event_type: str,
                        actor: str, data: dict) -> None:
    """Log an event to workflow_events."""
    import json
    with db() as conn:
        conn.execute(
            """INSERT INTO workflow_events (id, workflow_id, phase, event_type, actor, data_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), workflow_id, phase, event_type, actor, json.dumps(data)),
        )
