"""Phase 5 — Quote Negotiation Agent."""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.audit_tools import log_audit_event
from agencybill.database import db, row_to_dict


def _get_quotes(workflow_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? ORDER BY annual_premium",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def _present_quote(quote_id: str) -> dict:
    with db() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'presented', presented_at = datetime('now') WHERE id = ?",
            (quote_id,),
        )
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    return row_to_dict(row)


def _accept_quote(quote_id: str, workflow_id: str) -> dict:
    with db() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'accepted', accepted_at = datetime('now') WHERE id = ?",
            (quote_id,),
        )
        # Decline all other quotes
        conn.execute(
            "UPDATE quotes SET status = 'declined' WHERE workflow_id = ? AND id != ?",
            (workflow_id, quote_id),
        )
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    log_audit_event(workflow_id, "NegotiationAgent", "quote_accepted",
                    {"quote_id": quote_id})
    return row_to_dict(row)


def _decline_all_quotes(workflow_id: str, reason: str = "") -> dict:
    with db() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'declined' WHERE workflow_id = ?",
            (workflow_id,),
        )
    log_audit_event(workflow_id, "NegotiationAgent", "all_quotes_declined",
                    {"reason": reason})
    return {"workflow_id": workflow_id, "status": "all_declined", "reason": reason}


class NegotiationAgent(BaseAgent):
    """Phase 5: Present quote to broker, handle acceptance/negotiation."""

    name = "NegotiationAgent"
    system_prompt = """You are an insurance account manager handling Phase 5 Quote Negotiation.

Your tasks:
1. Read the policy (read_policy) and firm (read_firm) details
2. Get all generated quotes (get_quotes)
3. Present each quote to the broker by calling present_quote for each
4. Send a notification to the broker with the quote summary
5. Send a notification to the insured that renewal options are available
6. Accept the most appropriate quote (accept_quote) — choose the middle option (Option B)
   unless there are special circumstances. In this simulation, accept Option A (lowest)
   if the firm had claims, otherwise accept Option B.
7. Log a workflow event with the accepted quote details
8. Advance to BINDING phase

In a real system you would wait for the broker's response. Here, simulate the decision."""

    def _register_tools(self) -> None:
        self._add_tool("read_policy", "Read policy details",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy)
        self._add_tool("read_firm", "Read firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm)
        self._add_tool("get_quotes", "Get all quotes for the workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_quotes)
        self._add_tool("present_quote", "Mark a quote as presented to the broker",
            {"type": "object", "properties": {"quote_id": {"type": "string"}}, "required": ["quote_id"]},
            _present_quote)
        self._add_tool("accept_quote", "Accept a specific quote and decline all others",
            {"type": "object", "properties": {
                "quote_id": {"type": "string"},
                "workflow_id": {"type": "string"},
            }, "required": ["quote_id", "workflow_id"]},
            _accept_quote)
        self._add_tool("decline_all_quotes", "Decline all quotes (non-renewal path)",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "reason": {"type": "string"},
            }, "required": ["workflow_id"]},
            _decline_all_quotes)
        self._add_tool("send_notification", "Send notification to broker or insured",
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
        self._add_tool("advance_to_binding",
            "Advance workflow to BINDING phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "BINDING"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 5 Quote Negotiation for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Present quotes to broker and insured, accept the appropriate option, "
            "and advance to BINDING."
        )
        return self.run(msg)
