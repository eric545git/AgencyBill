"""Phase 1 — Pre-Renewal Triage Agent."""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import (
    read_policy, get_or_create_workflow, list_expiring_policies,
    update_workflow_phase, log_workflow_event,
)
from agencybill.tools.firm_tools import read_firm, read_attorneys, get_firm_practice_areas
from agencybill.tools.claims_tools import get_claims_for_firm, generate_loss_run
from agencybill.tools.audit_tools import log_audit_event


class PreRenewalAgent(BaseAgent):
    """
    Phase 1: Pull policy data, firm info, attorney roster, and claims history.
    Summarize the account and flag any immediate concerns.
    """

    name = "PreRenewalAgent"
    system_prompt = """You are an experienced insurance renewal specialist at a professional liability MGA.
Your job is Phase 1 of the agency bill renewal process for a Lawyers Professional Liability (LPL) policy.

Your tasks:
1. Read the policy details using read_policy
2. Read the insured law firm details using read_firm
3. Read the attorney roster using read_attorneys
4. Read the firm's claims history using get_claims_for_firm (last 5 years)
5. Generate a preliminary loss run summary using generate_loss_run
6. Log a workflow event summarizing your findings
7. Advance the workflow to the APPLICATION phase

Be thorough. Identify any red flags (high claims, unusual practice areas, large firm growth).
When done, provide a clear summary of the account for the underwriting team."""

    def _register_tools(self) -> None:
        self._add_tool(
            "read_policy",
            "Read full policy details including firm and broker info",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy,
        )
        self._add_tool(
            "read_firm",
            "Read law firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm,
        )
        self._add_tool(
            "read_attorneys",
            "Read all active attorneys for a firm",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_attorneys,
        )
        self._add_tool(
            "get_firm_practice_areas",
            "Get deduplicated practice areas across all firm attorneys",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            get_firm_practice_areas,
        )
        self._add_tool(
            "get_claims_for_firm",
            "Get claims history for a firm across all policies",
            {
                "type": "object",
                "properties": {
                    "firm_id": {"type": "string"},
                    "years": {"type": "integer", "default": 5},
                },
                "required": ["firm_id"],
            },
            get_claims_for_firm,
        )
        self._add_tool(
            "generate_loss_run",
            "Generate a loss run summary for underwriting (last N years)",
            {
                "type": "object",
                "properties": {
                    "firm_id": {"type": "string"},
                    "years": {"type": "integer", "default": 5},
                },
                "required": ["firm_id"],
            },
            generate_loss_run,
        )
        self._add_tool(
            "log_workflow_event",
            "Log a workflow event with findings",
            {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "phase": {"type": "string"},
                    "event_type": {"type": "string"},
                    "actor": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["workflow_id", "phase", "event_type", "actor", "data"],
            },
            log_workflow_event,
        )
        self._add_tool(
            "advance_to_application",
            "Advance the workflow to the APPLICATION phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "APPLICATION"),
        )

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 1 Pre-Renewal Triage for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Pull all policy, firm, attorney, and claims data. "
            "Summarize the account and advance to APPLICATION when done."
        )
        return self.run(msg)
