"""AM workbench: urgency ranking, decision enrichment, morning brief, quote deltas."""

from datetime import date, timedelta

from agencybill.tools.workbench_tools import (
    urgency_score, urgency_band, days_until, list_pending_decisions,
    morning_brief, enrich_market_quotes, next_pending_after, pause_context,
    get_worklist,
)
from agencybill.tools.policy_tools import get_or_create_workflow, update_workflow_status
from agencybill.tools.market_tools import (
    create_submission, record_market_quote, recommend_market_quote,
)
from agencybill.tools.audit_tools import create_checkpoint, resolve_checkpoint, log_audit_event
from agencybill.checkpoint import normalize_options
from agencybill.database import db


def test_urgency_score_prioritizes_near_expiry_and_alerts():
    calm = urgency_score(days_to_expiry=90, checkpoint_age_days=0, open_critical_alerts=0,
                         annual_premium=5000, phase="APPLICATION")
    hot = urgency_score(days_to_expiry=5, checkpoint_age_days=3, open_critical_alerts=2,
                        annual_premium=60000, phase="BINDING")
    expired = urgency_score(days_to_expiry=-2, checkpoint_age_days=1, open_critical_alerts=0,
                            annual_premium=10000, phase="QUOTE_COLLECTION")
    assert hot > calm
    assert expired > calm
    assert urgency_band(hot) in ("critical", "high")
    assert urgency_band(calm) in ("low", "medium")


def test_days_until():
    assert days_until((date.today() + timedelta(days=10)).isoformat()) == 10
    assert days_until(None) is None


def test_list_pending_decisions_ranked(tmp_db):
    wf_near = get_or_create_workflow("pol-1")
    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=80)).isoformat()

    with db() as conn:
        conn.execute(
            """INSERT INTO policies
               (id, firm_id, broker_id, policy_number, carrier,
                effective_date, expiration_date, limit_per_claim, aggregate_limit,
                deductible, annual_premium, status, attorney_count)
               VALUES ('pol-2', 'firm-1', 'brk-1', 'POL-002', 'Test Carrier',
                       '2025-01-01', ?, 1000000, 1000000, 5000, 8000, 'active', 1)""",
            (later,),
        )
        conn.execute(
            "UPDATE policies SET expiration_date = ?, annual_premium = 55000 WHERE id = 'pol-1'",
            (soon,),
        )

    wf_far = get_or_create_workflow("pol-2")

    opts = normalize_options([
        {"key": "accept", "label": "Approve"},
        {"key": "decline", "label": "Decline"},
    ])
    create_checkpoint(wf_near["id"], "UNDERWRITING", "Review UW?", opts)
    create_checkpoint(wf_far["id"], "UNDERWRITING", "Review UW far?", opts)

    ranked = list_pending_decisions(ranked=True)
    assert len(ranked) == 2
    assert ranked[0]["workflow_id"] == wf_near["id"]
    assert ranked[0]["urgency_score"] >= ranked[1]["urgency_score"]
    assert ranked[0]["days_to_expiry"] == 5
    assert "options" in ranked[0]
    assert ranked[0]["options"][0]["key"] == "accept"
    assert "risk" in ranked[0]
    assert ranked[0]["risk"]["total_claims"] == 0


def test_risk_chips_include_claims_and_uw(tmp_db):
    wf = get_or_create_workflow("pol-1")
    with db() as conn:
        conn.execute(
            """INSERT INTO claims
               (id, policy_id, firm_id, claim_date, status, amount_paid, amount_reserved, description)
               VALUES ('cl-1', 'pol-1', 'firm-1', '2024-06-01', 'open', 1000, 4000, 'malpractice')"""
        )
    log_audit_event(
        wf["id"], "UnderwritingAgent", "uw_decision",
        {"decision": "eligible", "risk_score": 4},
    )
    opts = normalize_options([
        {"key": "accept", "label": "Approve"},
        {"key": "decline", "label": "Decline"},
    ])
    create_checkpoint(wf["id"], "UNDERWRITING", "Review?", opts)

    ranked = list_pending_decisions(ranked=True)
    assert len(ranked) == 1
    risk = ranked[0]["risk"]
    assert risk["total_claims"] == 1
    assert risk["open_claims"] == 1
    assert risk["total_incurred"] == 5000
    assert risk["risk_score"] == 4
    assert risk["uw_decision"] == "eligible"
    assert ranked[0]["recommendation"]["kind"] == "underwriting"


def test_alternate_quotes_on_quote_collection(tmp_db):
    wf = get_or_create_workflow("pol-1")
    with db() as conn:
        conn.execute(
            "UPDATE renewal_workflows SET phase = 'QUOTE_COLLECTION' WHERE id = ?",
            (wf["id"],),
        )
        conn.execute(
            """INSERT INTO markets (id, name, contact_email, specialty, active)
               VALUES ('mkt-2', 'Pricey Market', 'p@example.com', 'LPL', 1)"""
        )
    sub = create_submission(wf["id"], "mkt-1")
    cheap = record_market_quote(
        submission_id=sub["id"], workflow_id=wf["id"], market_id="mkt-1",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=20000, carrier="Cheap Co",
    )
    sub2 = create_submission(wf["id"], "mkt-2")
    pricey = record_market_quote(
        submission_id=sub2["id"], workflow_id=wf["id"], market_id="mkt-2",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=30000, carrier="Pricey Co",
    )
    recommend_market_quote(cheap["id"], wf["id"], rationale="lowest")

    opts = normalize_options([
        {"key": "accept", "label": "Accept"},
        {"key": "hold", "label": "Hold"},
        {"key": "decline", "label": "Decline"},
    ])
    create_checkpoint(wf["id"], "QUOTE_COLLECTION", "Accept recommended?", opts)

    ranked = list_pending_decisions(ranked=True)
    assert len(ranked) == 1
    cp = ranked[0]
    assert cp["recommendation"]["kind"] == "quote"
    assert len(cp["alternate_quotes"]) == 2
    assert cp["alternate_quotes"][0]["id"] == cheap["id"]
    assert cp["alternate_quotes"][0]["is_recommended"] is True
    assert cp["alternate_quotes"][1]["id"] == pricey["id"]
    assert cp["alternate_quotes"][1]["is_recommended"] is False
    assert cp["premium_delta"] == -5000


def test_next_pending_after_skips_resolved(tmp_db):
    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=80)).isoformat()
    with db() as conn:
        conn.execute(
            """INSERT INTO policies
               (id, firm_id, broker_id, policy_number, carrier,
                effective_date, expiration_date, limit_per_claim, aggregate_limit,
                deductible, annual_premium, status, attorney_count)
               VALUES ('pol-2', 'firm-1', 'brk-1', 'POL-002', 'Test Carrier',
                       '2025-01-01', ?, 1000000, 1000000, 5000, 8000, 'active', 1)""",
            (later,),
        )
        conn.execute(
            "UPDATE policies SET expiration_date = ?, annual_premium = 55000 WHERE id = 'pol-1'",
            (soon,),
        )

    wf_a = get_or_create_workflow("pol-1")
    wf_b = get_or_create_workflow("pol-2")
    opts = normalize_options([
        {"key": "accept", "label": "Approve"},
        {"key": "decline", "label": "Decline"},
    ])
    cp_a = create_checkpoint(wf_a["id"], "UNDERWRITING", "First?", opts)
    cp_b = create_checkpoint(wf_b["id"], "UNDERWRITING", "Second?", opts)

    ranked = list_pending_decisions(ranked=True)
    assert ranked[0]["id"] == cp_a

    resolve_checkpoint(cp_a, "accept", notes="ok")
    nxt = next_pending_after(cp_a)
    assert nxt is not None
    assert nxt["id"] == cp_b

    resolve_checkpoint(cp_b, "accept", notes="ok")
    assert next_pending_after(cp_b) is None


def test_pause_context_labels(tmp_db):
    active = pause_context({"status": "active", "phase": "BINDING", "notes": ""})
    assert active["resume_label"] == "Run / Resume"

    waiting = pause_context({
        "status": "paused",
        "phase": "QUOTE_COLLECTION",
        "notes": "Paused: waiting for market quotes",
    })
    assert waiting["resume_label"] == "Resume quote collection"
    assert "Markets" in waiting["hint"]

    payment = pause_context({
        "status": "paused",
        "phase": "INVOICING",
        "notes": "Paused awaiting premium payment",
    })
    assert payment["resume_label"] == "Resume after payment"

    generic = pause_context({
        "status": "paused",
        "phase": "BINDING",
        "notes": "Something custom",
    })
    assert generic["resume_label"] == "Resume binding"
    assert generic["reason"] == "Something custom"


def test_get_worklist_expiring_and_paused(tmp_db):
    soon = (date.today() + timedelta(days=7)).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE policies SET expiration_date = ? WHERE id = 'pol-1'", (soon,)
        )
    wf = get_or_create_workflow("pol-1")
    update_workflow_status(wf["id"], "paused", "Paused: waiting for market quotes")

    exp = get_worklist("expiring_14")
    assert exp["kind"] == "expiring_14"
    assert any(i["id"] == "pol-1" for i in exp["entries"])

    paused = get_worklist("paused")
    assert len(paused["entries"]) == 1
    assert paused["entries"][0]["resume_label"] == "Resume quote collection"

    unknown = get_worklist("nope")
    assert unknown["entries"] == []


def test_quote_enrichment_and_recommendation(tmp_db):
    wf = get_or_create_workflow("pol-1")
    sub = create_submission(wf["id"], "mkt-1")
    cheap = record_market_quote(
        submission_id=sub["id"], workflow_id=wf["id"], market_id="mkt-1",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=20000, carrier="Cheap Co",
    )
    with db() as conn:
        conn.execute(
            """INSERT INTO markets (id, name, contact_email, specialty, active)
               VALUES ('mkt-2', 'Pricey Market', 'p@example.com', 'LPL', 1)"""
        )
    sub2 = create_submission(wf["id"], "mkt-2")
    record_market_quote(
        submission_id=sub2["id"], workflow_id=wf["id"], market_id="mkt-2",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=30000, carrier="Pricey Co",
    )
    recommend_market_quote(cheap["id"], wf["id"], rationale="lowest")

    quotes = enrich_market_quotes(wf["id"], expiring_premium=25000)
    assert len(quotes) == 2
    assert quotes[0]["annual_premium"] == 20000
    assert quotes[0]["is_best_premium"] is True
    assert quotes[0]["is_recommended"] is True
    assert quotes[0]["delta_vs_expiring"] == -5000


def test_morning_brief_shape(tmp_db):
    brief = morning_brief()
    for key in ("decisions_waiting", "expiring_14", "expiring_30", "stale_applications",
                "quotes_waiting", "critical_alerts", "top_decisions", "generated_at",
                "remittances_due", "paused", "active_workflows"):
        assert key in brief


def test_select_quote_and_decide_routes(tmp_db):
    """Web routes: alternate quote select + queue decide redirect to next item."""
    from fastapi.testclient import TestClient
    from agencybill.web.app import app

    wf = get_or_create_workflow("pol-1")
    with db() as conn:
        conn.execute(
            "UPDATE renewal_workflows SET phase = 'QUOTE_COLLECTION' WHERE id = ?",
            (wf["id"],),
        )
        conn.execute(
            """INSERT INTO markets (id, name, contact_email, specialty, active)
               VALUES ('mkt-2', 'Pricey Market', 'p@example.com', 'LPL', 1)"""
        )
        conn.execute(
            """INSERT INTO policies
               (id, firm_id, broker_id, policy_number, carrier,
                effective_date, expiration_date, limit_per_claim, aggregate_limit,
                deductible, annual_premium, status, attorney_count)
               VALUES ('pol-2', 'firm-1', 'brk-1', 'POL-002', 'Test Carrier',
                       '2025-01-01', ?, 1000000, 1000000, 5000, 8000, 'active', 1)""",
            ((date.today() + timedelta(days=80)).isoformat(),),
        )
    wf2 = get_or_create_workflow("pol-2")

    sub = create_submission(wf["id"], "mkt-1")
    cheap = record_market_quote(
        submission_id=sub["id"], workflow_id=wf["id"], market_id="mkt-1",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=20000, carrier="Cheap Co",
    )
    sub2 = create_submission(wf["id"], "mkt-2")
    pricey = record_market_quote(
        submission_id=sub2["id"], workflow_id=wf["id"], market_id="mkt-2",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=30000, carrier="Pricey Co",
    )
    recommend_market_quote(cheap["id"], wf["id"], rationale="lowest")

    opts = normalize_options([
        {"key": "accept", "label": "Accept"},
        {"key": "hold", "label": "Hold"},
        {"key": "decline", "label": "Decline"},
    ])
    cp_quote = create_checkpoint(wf["id"], "QUOTE_COLLECTION", "Accept?", opts)
    cp_uw = create_checkpoint(wf2["id"], "UNDERWRITING", "UW?", [
        {"key": "accept", "label": "Approve"},
        {"key": "decline", "label": "Decline"},
    ])

    client = TestClient(app)

    # Worklists route renders
    r = client.get("/worklists/paused")
    assert r.status_code == 200

    # Select alternate quote and accept HITL in one step
    r = client.post(
        f"/workflows/{wf['id']}/select-quote",
        data={
            "market_quote_id": pricey["id"],
            "checkpoint_id": cp_quote,
            "action": "accept",
            "next": "queue_next",
            "notes": "AM override",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/queue" in r.headers["location"]
    assert f"cp-{cp_uw}" in r.headers["location"] or "remaining=" in r.headers["location"]

    with db() as conn:
        wf_row = conn.execute(
            "SELECT recommended_market_quote_id FROM renewal_workflows WHERE id = ?",
            (wf["id"],),
        ).fetchone()
        assert wf_row["recommended_market_quote_id"] == pricey["id"]
        cp_row = conn.execute(
            "SELECT decision FROM human_checkpoints WHERE id = ?", (cp_quote,)
        ).fetchone()
        assert cp_row["decision"] == "accept"

    # Decide remaining checkpoint → queue cleared
    r = client.post(
        f"/queue/{cp_uw}/decide",
        data={"decision": "accept", "notes": "", "next": "queue_next"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "cleared=1" in r.headers["location"]
