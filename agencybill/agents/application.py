"""Phase 2 — Renewal Application Agent."""

import uuid
import json
from datetime import datetime

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm, read_attorneys
from agencybill.tools.document_tools import save_document
from agencybill.tools.notification_tools import send_notification
from agencybill.database import db


def _create_application(workflow_id: str, attorney_count: int,
                          gross_revenue: float, practice_areas: list,
                          claims_history_disclosed: bool) -> dict:
    app_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO applications
               (id, workflow_id, sent_at, attorney_count, gross_revenue,
                practice_areas_json, claims_history_disclosed)
               VALUES (?, ?, datetime('now'), ?, ?, ?, ?)""",
            (app_id, workflow_id, attorney_count, gross_revenue,
             json.dumps(practice_areas), 1 if claims_history_disclosed else 0),
        )
    return {"id": app_id, "workflow_id": workflow_id,
            "attorney_count": attorney_count, "status": "sent"}


def _receive_application(application_id: str, additional_notes: str = "") -> dict:
    with db() as conn:
        conn.execute(
            """UPDATE applications
               SET received_at = datetime('now'), completed = 1, additional_notes = ?
               WHERE id = ?""",
            (additional_notes, application_id),
        )
    return {"id": application_id, "status": "received", "completed": True}


def _get_application(workflow_id: str) -> dict:
    from agencybill.database import row_to_dict
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


class ApplicationAgent(BaseAgent):
    """Phase 2: Create renewal application, notify insured, track receipt."""

    name = "ApplicationAgent"
    system_prompt = """You are an insurance renewal coordinator handling Phase 2 of the LPL agency bill renewal.

Your tasks:
1. Read the policy and firm details
2. Read the current attorney roster
3. Create the renewal application record (create_application) using current firm data
4. Send the renewal questionnaire notification to the insured (send_notification)
5. Also notify the broker that the renewal application has been sent
6. Generate the renewal questionnaire document (save_document with 'renewal_questionnaire.html')
7. Mark the application as received (in a real workflow, you'd wait — here simulate receipt)
8. Log a workflow event
9. Advance to UNDERWRITING phase

Use professional, clear language in all notifications."""

    def _register_tools(self) -> None:
        self._add_tool("read_policy", "Read policy details",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy)
        self._add_tool("read_firm", "Read firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm)
        self._add_tool("read_attorneys", "Read attorney roster",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_attorneys)
        self._add_tool("create_application",
            "Create the renewal application record",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "attorney_count": {"type": "integer"},
                "gross_revenue": {"type": "number"},
                "practice_areas": {"type": "array", "items": {"type": "string"}},
                "claims_history_disclosed": {"type": "boolean"},
            }, "required": ["workflow_id", "attorney_count", "gross_revenue", "practice_areas", "claims_history_disclosed"]},
            _create_application)
        self._add_tool("receive_application",
            "Mark the application as received and complete",
            {"type": "object", "properties": {
                "application_id": {"type": "string"},
                "additional_notes": {"type": "string"},
            }, "required": ["application_id"]},
            _receive_application)
        self._add_tool("get_application", "Get the current application for a workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_application)
        self._add_tool("send_notification",
            "Send an email notification to insured or broker",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "channel": {"type": "string"},
            }, "required": ["workflow_id", "recipient", "subject", "body"]},
            send_notification)
        self._add_tool("save_document",
            "Render and save a document template",
            {"type": "object", "properties": {
                "template_name": {"type": "string"},
                "context": {"type": "object"},
                "filename_prefix": {"type": "string"},
            }, "required": ["template_name", "context"]},
            save_document)
        self._add_tool("log_workflow_event", "Log a workflow event",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"}, "phase": {"type": "string"},
                "event_type": {"type": "string"}, "actor": {"type": "string"},
                "data": {"type": "object"},
            }, "required": ["workflow_id", "phase", "event_type", "actor", "data"]},
            log_workflow_event)
        self._add_tool("advance_to_underwriting",
            "Advance the workflow to UNDERWRITING phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "UNDERWRITING"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 2 Renewal Application for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Create the application, send notifications, generate documents, and advance to UNDERWRITING."
        )
        return self.run(msg)
