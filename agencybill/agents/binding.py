"""Phase 6 — Binding & Policy Issuance Agent."""

import uuid
from datetime import datetime, date

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import (
    read_policy, update_workflow_phase, update_policy_status, log_workflow_event,
)
from agencybill.tools.firm_tools import read_firm, read_attorneys, read_broker
from agencybill.tools.document_tools import save_document
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.audit_tools import log_audit_event
from agencybill.database import db, row_to_dict


def _get_accepted_quote(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? AND status = 'accepted' LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


def _bind_policy(workflow_id: str, policy_id: str, quote_id: str) -> dict:
    """Create a binder record by updating the policy and logging the bind event."""
    binder_number = f"BND-{datetime.now().strftime('%Y%m%d')}-{workflow_id[:6].upper()}"

    # Compute new policy dates (1 year from expiration)
    with db() as conn:
        pol = conn.execute("SELECT expiration_date, carrier FROM policies WHERE id = ?",
                           (policy_id,)).fetchone()
    if pol:
        old_exp = pol["expiration_date"]
        effective_date = old_exp
        # Add 1 year
        try:
            from datetime import date as ddate
            ed = ddate.fromisoformat(old_exp)
            new_exp = ddate(ed.year + 1, ed.month, ed.day).isoformat()
        except Exception:
            new_exp = old_exp[:4+1] + str(int(old_exp[:4]) + 1) + old_exp[4:]
        carrier = pol["carrier"]
    else:
        effective_date = date.today().isoformat()
        new_exp = date(date.today().year + 1, date.today().month, date.today().day).isoformat()
        carrier = "Unknown"

    log_audit_event(workflow_id, "BindingAgent", "policy_bound",
                    {"binder_number": binder_number, "effective_date": effective_date,
                     "expiration_date": new_exp, "quote_id": quote_id})
    return {
        "binder_number": binder_number,
        "effective_date": effective_date,
        "expiration_date": new_exp,
        "carrier": carrier,
        "status": "bound",
    }


class BindingAgent(BaseAgent):
    """Phase 6: Bind the accepted quote, issue binder and policy declarations."""

    name = "BindingAgent"
    system_prompt = """You are an insurance operations specialist handling Phase 6 Binding & Policy Issuance.

Your tasks:
1. Read the policy (read_policy) and firm (read_firm) details
2. Get the accepted quote (get_accepted_quote)
3. Read the attorney roster (read_attorneys)
4. Read broker info if broker_id is available (read_broker)
5. Bind the policy (bind_policy) — this returns binder_number, effective_date, expiration_date
6. Generate the binder document (save_document with 'binder.html')
   Context needs: firm, policy, quote, broker, binder_number, effective_date, expiration_date
7. Generate the policy declarations page (save_document with 'policy_declarations.html')
   Context needs: firm, policy, quote, application (use attorney_count from policy),
   effective_date, expiration_date
8. Send notification to insured confirming binding with effective date
9. Send notification to broker confirming binding
10. Log a workflow event
11. Advance to INVOICING phase"""

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
        self._add_tool("read_broker", "Read broker details",
            {"type": "object", "properties": {"broker_id": {"type": "string"}}, "required": ["broker_id"]},
            read_broker)
        self._add_tool("get_accepted_quote", "Get the accepted quote for this workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_accepted_quote)
        self._add_tool("bind_policy", "Bind the policy — creates binder record",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "policy_id": {"type": "string"},
                "quote_id": {"type": "string"},
            }, "required": ["workflow_id", "policy_id", "quote_id"]},
            _bind_policy)
        self._add_tool("save_document", "Render and save a document",
            {"type": "object", "properties": {
                "template_name": {"type": "string"},
                "context": {"type": "object"},
                "filename_prefix": {"type": "string"},
            }, "required": ["template_name", "context"]},
            save_document)
        self._add_tool("send_notification", "Send notification",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "channel": {"type": "string"},
            }, "required": ["workflow_id", "recipient", "subject", "body"]},
            send_notification)
        self._add_tool("log_workflow_event", "Log a workflow event",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"}, "phase": {"type": "string"},
                "event_type": {"type": "string"}, "actor": {"type": "string"},
                "data": {"type": "object"},
            }, "required": ["workflow_id", "phase", "event_type", "actor", "data"]},
            log_workflow_event)
        self._add_tool("advance_to_invoicing",
            "Advance workflow to INVOICING phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "INVOICING"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 6 Binding & Policy Issuance for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Get the accepted quote, bind the policy, issue binder and declarations, "
            "notify parties, and advance to INVOICING."
        )
        return self.run(msg)
