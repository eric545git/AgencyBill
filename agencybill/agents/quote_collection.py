"""Phase 5 — Quote Collection Agent.

Tracks market responses, presents received quotes for comparison,
and recommends a winning quote. Acceptance happens after HITL confirmation.
"""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, log_workflow_event
from agencybill.tools.firm_tools import read_firm
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.market_tools import (
    get_submissions, get_submission_summary, get_market_quotes,
    recommend_market_quote, update_submission_status,
)


class QuoteCollectionAgent(BaseAgent):
    """
    Phase 5 — Quote Collection.

    Reviews market submissions and received quotes, then recommends a winner.
    The orchestrator accepts the quote only after the human checkpoint confirms.
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
6. Call recommend_market_quote with the winning market_quote_id and a short rationale
   - Do NOT call accept — the human must confirm first
7. Send a notification to the firm and broker summarizing the recommendation
8. Log a workflow event with the selection rationale

If NO quotes have been received yet:
- Log the pending status
- Send follow-up reminders to markets that haven't responded
- Do NOT recommend a quote
- Do NOT advance — the workflow will pause for human follow-up

Do NOT advance the workflow phase yourself — the orchestrator advances after HITL.

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
        self._add_tool("recommend_market_quote",
            "Recommend a market quote as the preferred option (does NOT accept — HITL confirms later)",
            {"type": "object", "properties": {
                "market_quote_id": {"type": "string"},
                "workflow_id": {"type": "string"},
                "rationale": {"type": "string"},
            }, "required": ["market_quote_id", "workflow_id"]},
            recommend_market_quote)
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

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 5 Quote Collection for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Review all received market quotes, compare them, recommend the best one, "
            "and notify the broker. Do not accept the quote yourself."
        )
        return self.run(msg)
