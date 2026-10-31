"""Orchestrator phase advancement and structured HITL decisions."""

from agencybill.config import NEXT_PHASE
from agencybill.orchestrator import WorkflowOrchestrator, CHECKPOINTS_AFTER, CHECKPOINTS_BEFORE
from agencybill.tools.policy_tools import (
    get_or_create_workflow, read_workflow, update_workflow_phase,
)
from agencybill.tools.market_tools import (
    create_submission, record_market_quote, recommend_market_quote,
)
from agencybill.checkpoint import normalize_options


class _StubAgent:
    """Agent that records it ran and returns a canned result."""

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        return f"stub-ok:{workflow_id}"


def test_checkpoint_options_use_stable_keys():
    for block in (CHECKPOINTS_BEFORE, CHECKPOINTS_AFTER):
        for phase, cp in block.items():
            opts = normalize_options(cp["options"])
            keys = {o["key"] for o in opts}
            assert keys, f"{phase} has no keys"
            # keys should be short identifiers, not long sentences
            for k in keys:
                assert " " not in k or k in ("non_renewal",)
                assert len(k) < 20


def test_orchestrator_advances_phases(tmp_db, monkeypatch):
    """With stub agents + auto_mode, orchestrator walks PRE_RENEWAL → COMPLETE."""
    import agencybill.orchestrator as orch_mod

    # Stub every agent class
    for phase in list(orch_mod.AGENT_MAP):
        orch_mod.AGENT_MAP[phase] = _StubAgent

    # Quote collection needs a recommendation before accept
    real_run = _StubAgent.run_phase

    def quote_run(self, policy_id, workflow_id):
        sub = create_submission(workflow_id, "mkt-1")
        mq = record_market_quote(
            submission_id=sub["id"], workflow_id=workflow_id, market_id="mkt-1",
            per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
            annual_premium=20000, carrier="Test Carrier",
        )
        recommend_market_quote(mq["id"], workflow_id, rationale="stub")
        return real_run(self, policy_id, workflow_id)

    # Binding needs an accepted quote (created by orchestrator after quote collection)
    def bind_run(self, policy_id, workflow_id):
        from agencybill.agents.binding import _bind_policy, _get_accepted_quote
        q = _get_accepted_quote(workflow_id)
        assert q, "expected accepted quote before binding"
        _bind_policy(workflow_id, policy_id, q["id"])
        return "bound"

    orch_mod.AGENT_MAP["QUOTE_COLLECTION"] = type(
        "QC", (_StubAgent,), {"run_phase": quote_run}
    )
    orch_mod.AGENT_MAP["BINDING"] = type(
        "BA", (_StubAgent,), {"run_phase": bind_run}
    )

    wf = get_or_create_workflow("pol-1")
    orchestrator = WorkflowOrchestrator(wf["id"], "pol-1", auto_mode=True)
    orchestrator.run()

    final = read_workflow(wf["id"])
    assert final["phase"] == "COMPLETE"
    assert final["status"] == "complete"


def test_legacy_quoting_phase_migrated(tmp_db, monkeypatch):
    import agencybill.orchestrator as orch_mod

    for phase in list(orch_mod.AGENT_MAP):
        orch_mod.AGENT_MAP[phase] = _StubAgent

    def quote_run(self, policy_id, workflow_id):
        sub = create_submission(workflow_id, "mkt-1")
        mq = record_market_quote(
            submission_id=sub["id"], workflow_id=workflow_id, market_id="mkt-1",
            per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
            annual_premium=20000, carrier="Test Carrier",
        )
        recommend_market_quote(mq["id"], workflow_id)
        return "ok"

    def bind_run(self, policy_id, workflow_id):
        from agencybill.agents.binding import _bind_policy, _get_accepted_quote
        q = _get_accepted_quote(workflow_id)
        _bind_policy(workflow_id, policy_id, q["id"])
        return "bound"

    orch_mod.AGENT_MAP["QUOTE_COLLECTION"] = type("QC", (_StubAgent,), {"run_phase": quote_run})
    orch_mod.AGENT_MAP["BINDING"] = type("BA", (_StubAgent,), {"run_phase": bind_run})

    wf = get_or_create_workflow("pol-1")
    update_workflow_phase(wf["id"], "QUOTING")  # legacy name

    orchestrator = WorkflowOrchestrator(wf["id"], "pol-1", auto_mode=True)
    orchestrator.run()

    final = read_workflow(wf["id"])
    assert final["phase"] == "COMPLETE"


def test_underwriting_tools_do_not_advance_to_quoting():
    from agencybill.agents.underwriting import UnderwritingAgent
    # Don't construct fully (needs API key path) — inspect class registration via a mock
    # Instead verify source-level: no QUOTING in module
    import agencybill.agents.underwriting as uw
    import inspect
    src = inspect.getsource(uw)
    assert "QUOTING" not in src
    assert "MARKET_SUBMISSION" in src or "orchestrator advances" in src.lower()


def test_next_phase_after_underwriting():
    assert NEXT_PHASE["UNDERWRITING"] == "MARKET_SUBMISSION"
