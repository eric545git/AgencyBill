"""WorkflowOrchestrator: drives phase-by-phase renewal workflow with HITL checkpoints."""

from agencybill.config import PHASES
from agencybill.tools.policy_tools import (
    read_workflow, update_workflow_phase, update_workflow_status, get_or_create_workflow,
)
from agencybill.tools.audit_tools import log_audit_event
from agencybill.checkpoint import HumanCheckpoint
from agencybill.display.console import (
    console, print_phase_banner, print_success, print_error, print_warning, print_info,
)

from agencybill.agents.pre_renewal import PreRenewalAgent
from agencybill.agents.application import ApplicationAgent
from agencybill.agents.underwriting import UnderwritingAgent
from agencybill.agents.quoting import QuotingAgent
from agencybill.agents.negotiation import NegotiationAgent
from agencybill.agents.binding import BindingAgent
from agencybill.agents.invoicing import InvoicingAgent
from agencybill.agents.remittance import RemittanceAgent


AGENT_MAP = {
    "PRE_RENEWAL":  PreRenewalAgent,
    "APPLICATION":  ApplicationAgent,
    "UNDERWRITING": UnderwritingAgent,
    "QUOTING":      QuotingAgent,
    "NEGOTIATION":  NegotiationAgent,
    "BINDING":      BindingAgent,
    "INVOICING":    InvoicingAgent,
    "REMITTANCE":   RemittanceAgent,
}

# Phases that require a human decision before the agent runs
CHECKPOINTS_BEFORE = {
    "BINDING": {
        "prompt": "The quote has been accepted. Confirm you want to BIND coverage.",
        "options": ["Confirm bind", "Hold — do not bind yet", "Decline — do not renew"],
    },
    "REMITTANCE": {
        "prompt": "Premium payment has been received. Confirm remittance to carrier.",
        "options": ["Confirm remittance", "Hold — follow up on payment first"],
    },
}

# Phases that require human review of agent output before advancing
CHECKPOINTS_AFTER = {
    "UNDERWRITING": {
        "prompt": "Underwriting has completed its review. What is the underwriting decision?",
        "options": ["Approve — proceed to quoting", "Refer — proceed with notation", "Decline renewal"],
    },
    "NEGOTIATION": {
        "prompt": "Quote has been presented. Has the broker/insured accepted?",
        "options": ["Accepted — proceed to binding", "Counter-offer — revise quote", "Declined — non-renewal"],
    },
    "INVOICING": {
        "prompt": "Invoice has been issued. Has premium payment been received?",
        "options": ["Payment received — proceed to remittance", "Awaiting payment — hold", "Non-payment — cancel"],
    },
}


class WorkflowOrchestrator:
    """Drives the full 8-phase LPL agency bill renewal workflow."""

    def __init__(self, workflow_id: str, policy_id: str, auto_mode: bool = False):
        self.workflow_id = workflow_id
        self.policy_id = policy_id
        self.auto_mode = auto_mode  # skip HITL if True
        self.checkpoint = HumanCheckpoint(workflow_id)

    def run(self) -> None:
        """Execute the workflow from current phase to completion."""
        workflow = read_workflow(self.workflow_id)
        current_phase = workflow.get("phase", "PRE_RENEWAL")

        if current_phase in ("COMPLETE", "NON_RENEWAL", "DECLINED"):
            print_info(f"Workflow is already in terminal state: {current_phase}")
            return

        print_info(f"Starting workflow from phase: {current_phase}")
        log_audit_event(self.workflow_id, "Orchestrator", "workflow_started",
                        {"from_phase": current_phase, "auto_mode": self.auto_mode})

        # Iterate through phases starting from current
        phase_list = list(AGENT_MAP.keys())
        try:
            start_idx = phase_list.index(current_phase)
        except ValueError:
            start_idx = 0

        for phase in phase_list[start_idx:]:
            workflow = read_workflow(self.workflow_id)
            current = workflow.get("phase")

            # If workflow was terminated by an agent, stop
            if workflow.get("status") in ("declined", "non_renewal", "cancelled"):
                print_warning(f"Workflow terminated: {workflow.get('status')}")
                self._handle_terminal(workflow.get("status"))
                return

            if current != phase:
                # Phase was already advanced by a previous agent — skip
                continue

            print_phase_banner(phase, self.workflow_id)

            # Pre-phase checkpoint
            if not self.auto_mode and phase in CHECKPOINTS_BEFORE:
                cp = CHECKPOINTS_BEFORE[phase]
                decision, _ = self.checkpoint.ask(phase, cp["prompt"], cp["options"])
                if "hold" in decision.lower() or "do not" in decision.lower():
                    print_warning(f"Workflow paused at {phase} checkpoint.")
                    update_workflow_status(self.workflow_id, "paused",
                                           f"Paused at {phase} pre-checkpoint")
                    return
                if "decline" in decision.lower():
                    self._handle_terminal("non_renewal")
                    return

            # Run the agent
            agent_cls = AGENT_MAP[phase]
            agent = agent_cls(self.workflow_id)
            try:
                result = agent.run_phase(self.policy_id, self.workflow_id)
                print_success(f"Phase {phase} completed.")
                log_audit_event(self.workflow_id, "Orchestrator", "phase_complete",
                                {"phase": phase, "result": result[:200] if result else ""})
            except Exception as exc:
                print_error(f"Phase {phase} failed: {exc}")
                log_audit_event(self.workflow_id, "Orchestrator", "phase_error",
                                {"phase": phase, "error": str(exc)})
                raise

            # Post-phase checkpoint
            if not self.auto_mode and phase in CHECKPOINTS_AFTER:
                cp = CHECKPOINTS_AFTER[phase]
                decision, _ = self.checkpoint.ask(phase, cp["prompt"], cp["options"])

                decision_lower = decision.lower()

                if phase == "UNDERWRITING":
                    if "decline" in decision_lower:
                        self._handle_terminal("non_renewal")
                        return
                    # eligible or refer — continue
                    if "refer" in decision_lower:
                        update_workflow_status(self.workflow_id, "active",
                                               "Referred — senior underwriter notified")

                elif phase == "NEGOTIATION":
                    if "counter" in decision_lower:
                        print_warning("Counter-offer requested. Pausing for quote revision.")
                        update_workflow_status(self.workflow_id, "paused",
                                               "Paused for quote counter-offer")
                        return
                    if "declined" in decision_lower:
                        self._handle_terminal("non_renewal")
                        return

                elif phase == "INVOICING":
                    if "awaiting" in decision_lower or "hold" in decision_lower:
                        update_workflow_status(self.workflow_id, "paused",
                                               "Paused awaiting premium payment")
                        return
                    if "cancel" in decision_lower or "non-payment" in decision_lower:
                        self._handle_terminal("cancelled")
                        return

            # Re-read workflow to see if agent advanced the phase
            workflow = read_workflow(self.workflow_id)

        # Check final state
        workflow = read_workflow(self.workflow_id)
        if workflow.get("status") == "complete":
            print_phase_banner("COMPLETE", self.workflow_id)
            print_success("Renewal workflow completed successfully!")
        else:
            print_info(f"Workflow ended in state: {workflow.get('status')}, phase: {workflow.get('phase')}")

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
                pass
        elif reason == "cancelled":
            update_workflow_status(self.workflow_id, "cancelled",
                                    "Cancelled: non-payment of premium")
            print_warning("Policy cancelled for non-payment.")
