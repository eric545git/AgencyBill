"""Phase 3 — Underwriting Review Agent."""

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm, read_attorneys, get_firm_practice_areas
from agencybill.tools.claims_tools import generate_loss_run, assess_risk, compute_experience_modifier
from agencybill.tools.document_tools import save_document
from agencybill.tools.audit_tools import log_audit_event
from agencybill.database import db, row_to_dict


def _get_application(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


def _record_uw_decision(workflow_id: str, decision: str, risk_score: float,
                         notes: str = "") -> dict:
    """Record the underwriting decision on the workflow."""
    from agencybill.tools.policy_tools import update_workflow_status
    if decision == "declined":
        update_workflow_status(workflow_id, "declined", notes)
    elif decision == "non_renewal":
        update_workflow_status(workflow_id, "non_renewal", notes)
    else:
        # eligible or refer — continue to quoting
        pass
    log_audit_event(workflow_id, "UnderwritingAgent", "uw_decision",
                    {"decision": decision, "risk_score": risk_score, "notes": notes})
    return {"workflow_id": workflow_id, "decision": decision, "risk_score": risk_score}


class UnderwritingAgent(BaseAgent):
    """Phase 3: Review application, assess risk, generate loss run, make eligibility determination."""

    name = "UnderwritingAgent"
    system_prompt = """You are a senior underwriter at a professional liability MGA specializing in Lawyers Professional Liability (LPL).

Your tasks for Phase 3 Underwriting Review:
1. Read the policy details (read_policy)
2. Read the firm and attorney information
3. Get the practice areas for the firm
4. Generate a loss run (generate_loss_run) for the past 5 years
5. Get the renewal application (get_application)
6. Assess the risk (assess_risk) using the practice areas, attorney count, and firm type
7. Compute an experience modifier (compute_experience_modifier) based on the loss run and current premium
8. Generate and save the underwriting memo document (save_document with 'underwriting_memo.html')
9. Record your underwriting decision (record_uw_decision):
   - "eligible": standard renewal, proceed to quoting
   - "refer": borderline — flag for senior review but continue to quoting
   - "declined": do not renew (only if risk_score >= 8.5 or total claims > 8)
10. If eligible or refer: advance to QUOTING phase
11. If declined: log a non-renewal event

Be rigorous. Use the actual data to make your determination."""

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
        self._add_tool("generate_loss_run", "Generate loss run summary",
            {"type": "object", "properties": {
                "firm_id": {"type": "string"},
                "years": {"type": "integer", "default": 5},
            }, "required": ["firm_id"]},
            generate_loss_run)
        self._add_tool("assess_risk", "Assess underwriting risk",
            {"type": "object", "properties": {
                "loss_run": {"type": "object"},
                "practice_areas": {"type": "array", "items": {"type": "string"}},
                "attorney_count": {"type": "integer"},
                "firm_type": {"type": "string"},
            }, "required": ["loss_run", "practice_areas", "attorney_count", "firm_type"]},
            assess_risk)
        self._add_tool("compute_experience_modifier", "Compute experience modifier from loss run",
            {"type": "object", "properties": {
                "loss_run": {"type": "object"},
                "current_premium": {"type": "number"},
            }, "required": ["loss_run", "current_premium"]},
            compute_experience_modifier)
        self._add_tool("get_application", "Get the renewal application",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_application)
        self._add_tool("save_document", "Render and save a document",
            {"type": "object", "properties": {
                "template_name": {"type": "string"},
                "context": {"type": "object"},
                "filename_prefix": {"type": "string"},
            }, "required": ["template_name", "context"]},
            save_document)
        self._add_tool("record_uw_decision", "Record the underwriting decision",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["eligible", "refer", "declined"]},
                "risk_score": {"type": "number"},
                "notes": {"type": "string"},
            }, "required": ["workflow_id", "decision", "risk_score"]},
            _record_uw_decision)
        self._add_tool("log_workflow_event", "Log a workflow event",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"}, "phase": {"type": "string"},
                "event_type": {"type": "string"}, "actor": {"type": "string"},
                "data": {"type": "object"},
            }, "required": ["workflow_id", "phase", "event_type", "actor", "data"]},
            log_workflow_event)
        self._add_tool("advance_to_quoting",
            "Advance workflow to QUOTING phase (use when eligible or refer)",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "QUOTING"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 3 Underwriting Review for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Review all available data, generate the loss run, assess risk, "
            "make your eligibility determination, and advance the workflow appropriately."
        )
        return self.run(msg)
