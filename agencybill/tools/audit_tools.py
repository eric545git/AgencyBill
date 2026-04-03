"""Audit trail logging and retrieval."""

import uuid
import json
from datetime import datetime

from agencybill.database import db, row_to_dict


def log_audit_event(workflow_id: str | None, agent_name: str,
                     action: str, details: dict) -> None:
    """Append an event to the audit trail."""
    with db() as conn:
        conn.execute(
            """INSERT INTO audit_events (id, workflow_id, agent_name, action, details_json)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), workflow_id, agent_name, action, json.dumps(details)),
        )


def get_audit_trail(workflow_id: str) -> list[dict]:
    """Return full audit trail for a workflow, newest first."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE workflow_id = ? ORDER BY created_at",
            (workflow_id,),
        ).fetchall()
    results = []
    for row in rows:
        d = row_to_dict(row)
        if d.get("details_json"):
            try:
                d["details"] = json.loads(d["details_json"])
            except Exception:
                d["details"] = {}
        results.append(d)
    return results


def get_checkpoints(workflow_id: str) -> list[dict]:
    """Return all human checkpoints for a workflow."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM human_checkpoints WHERE workflow_id = ? ORDER BY created_at",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def create_checkpoint(workflow_id: str, phase: str, prompt: str,
                       options: list[str]) -> str:
    """Create a human checkpoint record. Returns the checkpoint ID."""
    cp_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO human_checkpoints (id, workflow_id, phase, prompt, options_json)
               VALUES (?, ?, ?, ?, ?)""",
            (cp_id, workflow_id, phase, prompt, json.dumps(options)),
        )
    return cp_id


def resolve_checkpoint(checkpoint_id: str, decision: str,
                         decided_by: str = "human", notes: str = "") -> None:
    """Record the human decision on a checkpoint."""
    with db() as conn:
        conn.execute(
            """UPDATE human_checkpoints
               SET decision = ?, decided_by = ?, decided_at = datetime('now'), notes = ?
               WHERE id = ?""",
            (decision, decided_by, notes, checkpoint_id),
        )
