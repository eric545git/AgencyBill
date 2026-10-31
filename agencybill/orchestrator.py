"""WorkflowOrchestrator: drives phase-by-phase renewal workflow with HITL checkpoints."""

from agencybill.config import AGENT_PHASES, NEXT_PHASE, PHASE_LABELS
from agencybill.tools.policy_tools import (
    read_workflow, update_workflow_phase, update_workflow_status,
)
from agencybill.tools.audit_tools import log_audit_event
from agencybill.tools.market_tools import (
    accept_market_quote, get_recommended_market_quote_id,
)
from agencybill.checkpoint import HumanCheckpoint
from agencybill.display.console import (
    print_phase_banner, print_success, print_error, print_warning, print_info,
)
from agencybill.logging_config import get_logger

from agencybill.agents.pre_renewal import PreRenewalAgent
from agencybill.agents.application import ApplicationAgent
from agencybill.agents.underwriting import UnderwritingAgent
from agencybill.agents.market_submission import MarketSubmissionAgent
from agencybill.agents.quote_collection import QuoteCollectionAgent
from agencybill.agents.binding import BindingAgent
from agencybill.agents.invoicing import InvoicingAgent
from agencybill.agents.remittance import RemittanceAgent

log = get_logger("agencybill.orchestrator")


AGENT_MAP = {
    "PRE_RENEWAL":       PreRenewalAgent,
    "APPLICATION":       ApplicationAgent,
    "UNDERWRITING":      UnderwritingAgent,
    "MARKET_SUBMISSION": MarketSubmissionAgent,
    "QUOTE_COLLECTION":  QuoteCollectionAgent,
    "BINDING":           BindingAgent,
    "INVOICING":         InvoicingAgent,
    "REMITTANCE":        RemittanceAgent,
}

# Phases that require a human decision before the agent runs
# Options use stable keys; labels are shown in CLI/UI
CHECKPOINTS_BEFORE = {
    "BINDING": {
        "prompt": "The quote has been accepted. Confirm you want to BIND coverage.",
        "options": [
            {"key": "confirm", "label": "Confirm bind"},
            {"key": "hold", "label": "Hold — do not bind yet"},
            {"key": "decline", "label": "Decline — do not renew"},
        ],
    },
    "REMITTANCE": {
        "prompt": "Premium payment has been received. Confirm remittance to carrier.",
        "options": [
            {"key": "confirm", "label": "Confirm remittance"},
            {"key": "hold", "label": "Hold — follow up on payment first"},
        ],
    },
}

# Phases that require human review of agent output before advancing
CHECKPOINTS_AFTER = {
    "UNDERWRITING": {
        "prompt": "Underwriting has completed its review. What is the underwriting decision?",
        "options": [
            {"key": "accept", "label": "Approve — proceed to market submission"},
            {"key": "refer", "label": "Refer — proceed with notation"},
            {"key": "decline", "label": "Decline renewal"},
        ],
    },
    "QUOTE_COLLECTION": {
        "prompt": "Market quotes have been compared. Accept the recommended quote and proceed to binding?",
        "options": [
            {"key": "accept", "label": "Accepted — proceed to binding"},
            {"key": "hold", "label": "Hold — revise selection"},
            {"key": "decline", "label": "Declined — non-renewal"},
        ],
    },
    "INVOICING": {
        "prompt": "Invoice has been issued. Has premium payment been received?",
        "options": [
            {"key": "accept", "label": "Payment received — proceed to remittance"},
            {"key": "awaiting", "label": "Awaiting payment — hold"},
            {"key": "cancel", "label": "Non-payment — cancel"},
        ],
    },
}


class WorkflowOrchestrator:
    """Drives the full 8-phase LPL agency bill renewal workflow."""

    def __init__(self, workflow_id: str, policy_id: str,
                 auto_mode: bool = False, web_mode: bool = False):
        self.workflow_id = workflow_id
        self.policy_id = policy_id
        self.auto_mode = auto_mode
        self.web_mode = web_mode
        self.checkpoint = HumanCheckpoint(workflow_id, web_mode=web_mode)

    def run(self) -> None:
        """Execute the workflow from current phase to completion."""
        workflow = read_workflow(self.workflow_id)
        current_phase = workflow.get("phase", "PRE_RENEWAL")

        if current_phase in ("COMPLETE", "NON_RENEWAL", "DECLINED"):
            print_info(f"Workflow is already in terminal state: {current_phase}")
            return

        # Recover from legacy QUOTING/NEGOTIATION phase names
        legacy_map = {"QUOTING": "MARKET_SUBMISSION", "NEGOTIATION": "QUOTE_COLLECTION"}
        if current_phase in legacy_map:
            fixed = legacy_map[current_phase]
            log.warning("Migrating legacy phase %s → %s", current_phase, fixed)
            update_workflow_phase(self.workflow_id, fixed)
            current_phase = fixed

        print_info(f"Starting workflow from phase: {current_phase}")
        log_audit_event(self.workflow_id, "Orchestrator", "workflow_started",
                        {"from_phase": current_phase, "auto_mode": self.auto_mode})

        try:
            start_idx = AGENT_PHASES.index(current_phase)
        except ValueError:
            start_idx = 0

        for phase in AGENT_PHASES[start_idx:]:
            workflow = read_workflow(self.workflow_id)
            current = workflow.get("phase")

            if workflow.get("status") in ("declined", "non_renewal", "cancelled", "complete"):
                print_warning(f"Workflow terminated: {workflow.get('status')}")
                self._handle_terminal(workflow.get("status"))
                return

            if current != phase:
                # Already advanced (e.g. resumed mid-stream) — skip
                continue

            print_phase_banner(phase, self.workflow_id)

            # Pre-phase checkpoint
            if not self.auto_mode and phase in CHECKPOINTS_BEFORE:
                cp = CHECKPOINTS_BEFORE[phase]
                decision, _ = self.checkpoint.ask(phase, cp["prompt"], cp["options"])
                if decision == "hold":
                    print_warning(f"Workflow paused at {phase} checkpoint.")
                    update_workflow_status(self.workflow_id, "paused",
                                           f"Paused at {phase} pre-checkpoint")
                    return
                if decision == "decline":
                    self._handle_terminal("non_renewal")
                    return

            # Run the agent
            agent_cls = AGENT_MAP[phase]
            agent = agent_cls(self.workflow_id)
            try:
                result = agent.run_phase(self.policy_id, self.workflow_id)
                print_success(f"Phase {phase} completed.")
                log_audit_event(self.workflow_id, "Orchestrator", "phase_complete",
                                {"phase": phase, "result": (result or "")[:200]})
            except Exception as exc:
                print_error(f"Phase {phase} failed: {exc}")
                log.exception("Phase %s failed", phase)
                log_audit_event(self.workflow_id, "Orchestrator", "phase_error",
                                {"phase": phase, "error": str(exc)})
                raise

            # Agent may have declined mid-phase
            workflow = read_workflow(self.workflow_id)
            if workflow.get("status") in ("declined", "non_renewal", "cancelled"):
                self._handle_terminal(workflow.get("status"))
                return

            # Quote collection requires a recommendation before HITL / advance
            if phase == "QUOTE_COLLECTION":
                if not get_recommended_market_quote_id(self.workflow_id):
                    print_warning("No quote recommended yet — pausing for market responses.")
                    update_workflow_status(self.workflow_id, "paused",
                                           "Paused: waiting for market quotes")
                    return

            # Post-phase checkpoint
            if not self.auto_mode and phase in CHECKPOINTS_AFTER:
                cp = CHECKPOINTS_AFTER[phase]
                decision, _ = self.checkpoint.ask(phase, cp["prompt"], cp["options"])

                if phase == "UNDERWRITING":
                    if decision == "decline":
                        self._handle_terminal("non_renewal")
                        return
                    if decision == "refer":
                        update_workflow_status(self.workflow_id, "active",
                                               "Referred — senior underwriter notified")

                elif phase == "QUOTE_COLLECTION":
                    if decision == "hold":
                        print_warning("Quote selection on hold. Pausing for review.")
                        update_workflow_status(self.workflow_id, "paused",
                                               "Paused for quote selection review")
                        return
                    if decision == "decline":
                        self._handle_terminal("non_renewal")
                        return
                    # accept — finalize the recommended quote now (after HITL)
                    if not self._accept_recommended_quote():
                        print_error("No recommended quote to accept. Pausing.")
                        update_workflow_status(self.workflow_id, "paused",
                                               "Paused: missing recommended quote")
                        return

                elif phase == "INVOICING":
                    if decision in ("awaiting", "hold"):
                        update_workflow_status(self.workflow_id, "paused",
                                               "Paused awaiting premium payment")
                        return
                    if decision == "cancel":
                        self._handle_terminal("cancelled")
                        return
            elif self.auto_mode and phase == "QUOTE_COLLECTION":
                if not self._accept_recommended_quote():
                    print_error("No recommended quote to accept in auto mode.")
                    update_workflow_status(self.workflow_id, "paused",
                                           "Paused: missing recommended quote")
                    return

            # Orchestrator owns phase advancement
            self._advance_phase(phase)

        workflow = read_workflow(self.workflow_id)
        if workflow.get("status") == "complete" or workflow.get("phase") == "COMPLETE":
            print_phase_banner("COMPLETE", self.workflow_id)
            print_success("Renewal workflow completed successfully!")
        else:
            print_info(
                f"Workflow ended in state: {workflow.get('status')}, "
                f"phase: {workflow.get('phase')}"
            )

    def _accept_recommended_quote(self) -> bool:
        quote_id = get_recommended_market_quote_id(self.workflow_id)
        if not quote_id:
            log.error("No recommended_market_quote_id on workflow %s", self.workflow_id)
            return False
        result = accept_market_quote(quote_id, self.workflow_id)
        if result.get("error"):
            log.error("accept_market_quote failed: %s", result)
            return False
        log_audit_event(self.workflow_id, "Orchestrator", "quote_accepted_after_hitl",
                        {"market_quote_id": quote_id, "result": result})
        print_success(f"Accepted market quote {quote_id[:8]}…")
        return True

    def _advance_phase(self, completed_phase: str) -> None:
        """Advance to the next phase if still on completed_phase."""
        workflow = read_workflow(self.workflow_id)
        if workflow.get("phase") != completed_phase:
            # Agent already advanced (legacy tools) — trust it if valid
            log.info(
                "Phase already moved from %s to %s",
                completed_phase, workflow.get("phase"),
            )
            return

        next_phase = NEXT_PHASE.get(completed_phase)
        if not next_phase:
            return

        if next_phase == "COMPLETE":
            update_workflow_status(self.workflow_id, "complete",
                                   "Renewal workflow completed successfully")
            update_workflow_phase(self.workflow_id, "COMPLETE")
        else:
            # Ensure status is active when moving forward after a pause resume
            if workflow.get("status") == "paused":
                update_workflow_status(self.workflow_id, "active")
            update_workflow_phase(self.workflow_id, next_phase)

        log_audit_event(self.workflow_id, "Orchestrator", "phase_advanced",
                        {"from": completed_phase, "to": next_phase})
        print_info(f"Advanced to {PHASE_LABELS.get(next_phase, next_phase)}")

    def _handle_terminal(self, reason: str) -> None:
        """Handle non-renewal or declination."""
        from agencybill.tools.document_tools import save_document
        from agencybill.tools.policy_tools import read_policy
        from agencybill.tools.firm_tools import read_firm

        if reason in ("non_renewal", "declined"):
            update_workflow_status(self.workflow_id, reason,
                                    f"Workflow terminated: {reason}")
            update_workflow_phase(self.workflow_id, "NON_RENEWAL")
            print_warning(f"Workflow terminated: {reason.upper()}")
            try:
                policy = read_policy(self.policy_id)
                firm = read_firm(policy.get("firm_id", ""))
                save_document("non_renewal_notice.html",
                              {"firm": firm, "policy": policy, "reason": reason},
                              filename_prefix="non_renewal")
                print_info("Non-renewal notice generated in output/")
            except Exception:
                log.exception("Failed to generate non-renewal notice")
        elif reason == "cancelled":
            update_workflow_status(self.workflow_id, "cancelled",
                                    "Cancelled: non-payment of premium")
            print_warning("Policy cancelled for non-payment.")
        elif reason == "complete":
            pass
