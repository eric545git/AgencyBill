"""Phase 7 — Invoicing & Premium Collection Agent."""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm, read_broker
from agencybill.tools.payment_tools import (
    create_invoice, get_invoices_for_workflow, record_payment, get_invoice,
)
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


class InvoicingAgent(BaseAgent):
    """Phase 7: Create invoice, notify broker, simulate payment receipt."""

    name = "InvoicingAgent"
    system_prompt = """You are an insurance billing specialist handling Phase 7 Invoicing & Premium Collection.

This is an AGENCY BILL workflow: the broker/agent collects premium from the insured and remits to the carrier.

Your tasks:
1. Read the policy (read_policy) and firm (read_firm) details
2. Get the accepted quote (get_accepted_quote) for the premium amount
3. Read broker info if broker_id is available (read_broker)
4. Create an invoice (create_invoice) for the annual premium amount, due in 30 days
5. Generate the invoice document (save_document with 'invoice.html')
   Context needs: invoice, firm, policy, broker, effective_date, expiration_date
   You can get effective_date/expiration_date from the policy expiration_date (new period starts at expiration)
6. Send the invoice notification to the broker (they pay us in agency bill)
7. Send a copy notification to the insured
8. Simulate payment receipt: call record_payment for the full invoice amount
   (In a real system, you'd wait for actual payment)
9. Log a workflow event confirming payment received
10. Advance to REMITTANCE phase"""

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
        self._add_tool("create_invoice", "Create a premium invoice",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "amount_due": {"type": "number"},
                "days_until_due": {"type": "integer"},
            }, "required": ["workflow_id", "amount_due"]},
            create_invoice)
        self._add_tool("get_invoices_for_workflow", "Get all invoices for a workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_invoices_for_workflow)
        self._add_tool("record_payment", "Record a premium payment",
            {"type": "object", "properties": {
                "invoice_id": {"type": "string"},
                "amount": {"type": "number"},
                "method": {"type": "string"},
                "reference": {"type": "string"},
            }, "required": ["invoice_id", "amount"]},
            record_payment)
        self._add_tool("save_document", "Render and save invoice document",
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
        self._add_tool("advance_to_remittance",
            "Advance workflow to REMITTANCE phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "REMITTANCE"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 7 Invoicing & Premium Collection for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Create the invoice, generate invoice document, notify broker and insured, "
            "simulate payment receipt, and advance to REMITTANCE."
        )
        return self.run(msg)
