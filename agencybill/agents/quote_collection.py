"""Phase 5 — Quote Collection Agent.

Tracks market responses, presents received quotes for comparison,
and works with the human to select the winning quote for binding.
"""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.audit_tools import log_audit_event
from agencybill.tools.market_tools import (
    get_submissions, get_submission_summary, get_market_quotes,
    accept_market_quote, update_submission_status,
)


class QuoteCollectionAgent(BaseAgent):
    """
    Phase 5 — Quote Collection.

    Reviews the market submissions and received quotes. Presents a comparison
    of all received quotes. Once the human selects a winner via the HITL queue,
    marks it accepted and advances to BINDING.

    Note: In web mode, quote entry happens directly through the web UI — humans
    record quotes as they arrive from markets. This agent runs after the user
    signals they are ready to compare and select.
    """

    name = "QuoteCollectionAgent"
    system_prompt = """You are a wholesale placement specialist handling Phase 5 Quote Collection for an LPL renewal.

Markets have received the submission and some have responded with quotes. Your job is to:

1. Read the policy (read_policy) and firm (read_firm) details
2. Get submission statuses (get_submissions) to see which markets have responded
3. Get all received market quotes (get_market_quotes)
4. Review the quotes and provide a professional comparison:
   - List each quote by market, premium, limits, deductible
   - Highlight the best value option
   - Note any important coverage differences (exclusions, retroactive dates)
   - Flag any concerns about coverage gaps
5. If there are quotes to compare: identify the recommended quote and explain why
6. Accept the recommended/best quote (accept_market_quote)
   - In a real workflow the broker confirms — here accept the best premium with broadest coverage
   - "Best" means: lowest premium for equivalent coverage terms, OR best coverage at similar premium
7. Send a notification to the firm and broker confirming the selected quote
8. Log a workflow event with the selection rationale
9. Advance to BINDING phase

If NO quotes have been received yet:
- Log the pending status
- Send follow-up reminders to markets that haven't responded
- Do NOT advance — the orchestrator will handle the HITL wait

Quote comparison criteria for LPL:
- Annual premium (primary)
- Per-claim and aggregate limits
- Deductible amount
- Retroactive date coverage (broader = better)
- Prior acts coverage
- Notable exclusions (fewer = better)
- Carrier financial strength (AM Best rating matters)"""

    def _register_tools(self) -> None:
        self._add_tool("read_policy", "Read policy details",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy)
        self._add_tool("read_firm", "Read firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm)
        self._add_tool("get_submissions", "Get all market submissions and their statuses",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_submissions)
        self._add_tool("get_submission_summary", "Get a summary of submission statuses",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_submission_summary)
        self._add_tool("get_market_quotes", "Get all market quotes received for this workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_market_quotes)
        self._add_tool("accept_market_quote",
            "Accept a market quote as the winning bid (also creates a quotes record for binding)",
            {"type": "object", "properties": {
                "market_quote_id": {"type": "string"},
                "workflow_id": {"type": "string"},
            }, "required": ["market_quote_id", "workflow_id"]},
            accept_market_quote)
        self._add_tool("update_submission_status",
            "Update the status of a market submission (e.g., mark as declined or no_response)",
            {"type": "object", "properties": {
                "submission_id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["submitted", "quoted", "declined", "passed", "bound", "no_response"]},
                "reason": {"type": "string"},
            }, "required": ["submission_id", "status"]},
            update_submission_status)
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
            "Advance workflow to BINDING phase after quote is accepted",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "BINDING"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 5 Quote Collection for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Review all received market quotes, compare them, accept the best one, "
            "notify the broker, and advance to BINDING."
        )
        return self.run(msg)
