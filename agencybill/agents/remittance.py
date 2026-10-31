"""Phase 8 — Premium Remittance Agent."""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, log_workflow_event
from agencybill.tools.firm_tools import read_firm, read_broker
from agencybill.tools.payment_tools import (
    create_remittance, mark_remittance_sent, get_remittances, get_invoices_for_workflow,
    resolve_commission_pct,
)
from agencybill.tools.notification_tools import send_notification
from agencybill.database import db, row_to_dict


def _get_accepted_quote(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? AND status = 'accepted' LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


class RemittanceAgent(BaseAgent):
    """Phase 8: Calculate and process premium remittance to carrier."""

    name = "RemittanceAgent"
    system_prompt = """You are an insurance accounting specialist handling Phase 8 Premium Remittance.

In agency bill, the broker has collected the premium from the insured. Now you must:
1. Read the policy (read_policy) details (includes carrier name and broker_id)
2. Read the firm (read_firm) details
3. Get the accepted quote (get_accepted_quote) for the gross premium
4. Get all invoices for the workflow (get_invoices_for_workflow) to confirm payment
5. Resolve the commission rate (resolve_commission_pct) using the policy's broker_id
6. Create a remittance record (create_remittance) for the carrier using that commission_pct
7. Mark the remittance as sent (mark_remittance_sent)
8. Send a notification to the carrier confirming remittance
9. Log a workflow event with remittance details

Do NOT advance or complete the workflow yourself — the orchestrator marks COMPLETE.

Provide a final summary of the renewal transaction:
- Gross premium collected
- Net commission retained
- Amount remitted to carrier
- Policy effective dates"""

    def _register_tools(self) -> None:
        self._add_tool("read_policy", "Read policy details",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy)
        self._add_tool("read_firm", "Read firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm)
        self._add_tool("read_broker", "Read broker details",
            {"type": "object", "properties": {"broker_id": {"type": "string"}}, "required": ["broker_id"]},
            read_broker)
        self._add_tool("get_accepted_quote", "Get the accepted quote",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_accepted_quote)
        self._add_tool("get_invoices_for_workflow", "Get all invoices for this workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_invoices_for_workflow)
        self._add_tool("resolve_commission_pct",
            "Resolve commission percent for a broker (falls back to default)",
            {"type": "object", "properties": {"broker_id": {"type": "string"}}, "required": []},
            resolve_commission_pct)
        self._add_tool("create_remittance", "Create carrier remittance record",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "carrier": {"type": "string"},
                "gross_premium": {"type": "number"},
                "commission_pct": {"type": "number"},
            }, "required": ["workflow_id", "carrier", "gross_premium"]},
            create_remittance)
        self._add_tool("mark_remittance_sent", "Mark remittance as sent to carrier",
            {"type": "object", "properties": {"remittance_id": {"type": "string"}}, "required": ["remittance_id"]},
            mark_remittance_sent)
        self._add_tool("get_remittances", "Get all remittances for a workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_remittances)
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

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 8 Premium Remittance for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Confirm payment received, create remittance to carrier, and mark as sent."
        )
        return self.run(msg)
