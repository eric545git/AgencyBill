"""Phase 4 — Market Submission Agent.

Selects appropriate wholesalers/program administrators, creates submission
records, and sends the account package to each market for quoting.
"""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm, read_attorneys, get_firm_practice_areas
from agencybill.tools.claims_tools import generate_loss_run
from agencybill.tools.notification_tools import send_notification
from agencybill.tools.audit_tools import log_audit_event
from agencybill.tools.market_tools import (
    list_markets, select_markets_for_account, create_submission,
    get_submissions, get_submission_summary,
)
from agencybill.database import db, row_to_dict


def _get_application(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


class MarketSubmissionAgent(BaseAgent):
    """
    Phase 4 — Market Submission.

    Reviews the underwritten account and selects 3-6 appropriate wholesale
    markets / program administrators to approach for pricing. Creates a
    submission record for each and sends the account package via email.
    """

    name = "MarketSubmissionAgent"
    system_prompt = """You are a wholesale broker/MGA placement specialist handling Phase 4 Market Submission for a Lawyers Professional Liability (LPL) renewal.

Your role is to identify the best markets to approach for this account and submit it for pricing. You are NOT calculating premiums yourself — you are sending the account to wholesale markets and program administrators who will return their own quotes.

Your tasks:
1. Read the policy (read_policy) and firm (read_firm) details
2. Get the renewal application data (get_application)
3. Get the firm's practice areas (get_firm_practice_areas)
4. Generate the loss run summary (generate_loss_run) — key underwriting info for markets
5. View available markets (list_markets) to understand who we can submit to
6. Select appropriate markets (select_markets_for_account) by providing:
   - attorney_count: from the application or policy
   - state: from the firm record
   - firm_type: from the firm record
   - practice_areas: list of practice areas
   - estimated_premium: the prior year's premium from the policy record as a reference point
7. For each selected market (aim for 3-5 markets), create a submission record (create_submission)
   with a professional submission_notes describing the account
8. For each submission, send a notification email to the market contact (send_notification)
   - Recipient: the contact_email from the market record
   - Subject: "LPL Renewal Submission — [Firm Name] — Expiring [date]"
   - Body: Professional submission email with key account details:
     * Firm name, state, years in business
     * Attorney count and practice areas
     * Current limits/deductible
     * Prior premium
     * 5-year loss summary
     * Requesting quote by [date 10 business days out]
9. Log a workflow event with the submission summary (get_submission_summary)
10. Advance to QUOTE_COLLECTION phase

Select markets that are a good fit for the risk profile. For high-risk accounts (securities, large claims history), include E&S markets. For clean accounts, prioritize preferred/standard markets.

Be professional and thorough in your submission emails."""

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
        self._add_tool("get_firm_practice_areas", "Get firm practice areas",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            get_firm_practice_areas)
        self._add_tool("generate_loss_run", "Generate 5-year loss run summary",
            {"type": "object", "properties": {
                "firm_id": {"type": "string"},
                "years": {"type": "integer", "default": 5},
            }, "required": ["firm_id"]},
            generate_loss_run)
        self._add_tool("get_application", "Get the renewal application",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_application)
        self._add_tool("list_markets", "List all available wholesale markets and program admins",
            {"type": "object", "properties": {
                "active_only": {"type": "boolean", "default": True},
            }},
            list_markets)
        self._add_tool("select_markets_for_account",
            "Filter markets to find those appropriate for this account profile",
            {"type": "object", "properties": {
                "attorney_count": {"type": "integer"},
                "state": {"type": "string"},
                "firm_type": {"type": "string"},
                "practice_areas": {"type": "array", "items": {"type": "string"}},
                "estimated_premium": {"type": "number"},
            }, "required": ["attorney_count", "state", "firm_type", "practice_areas"]},
            select_markets_for_account)
        self._add_tool("create_submission",
            "Create a market submission record for one market",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "market_id": {"type": "string"},
                "submission_notes": {"type": "string"},
            }, "required": ["workflow_id", "market_id"]},
            create_submission)
        self._add_tool("get_submissions", "Get all submissions created for a workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_submissions)
        self._add_tool("get_submission_summary", "Get a summary of submission statuses",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            get_submission_summary)
        self._add_tool("send_notification",
            "Send a submission email to a market contact",
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
        self._add_tool("advance_to_quote_collection",
            "Advance workflow to QUOTE_COLLECTION phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "QUOTE_COLLECTION"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 4 Market Submission for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Review the account, select appropriate markets, create submissions, "
            "send submission emails, and advance to QUOTE_COLLECTION."
        )
        return self.run(msg)
