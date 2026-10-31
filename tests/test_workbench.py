"""AM workbench: urgency ranking, decision enrichment, morning brief, quote deltas."""

from agencybill.tools.workbench_tools import (
    urgency_score, urgency_band, days_until, list_pending_decisions,
    morning_brief, enrich_market_quotes,
)
from agencybill.tools.policy_tools import get_or_create_workflow
from agencybill.tools.market_tools import (
    create_submission, record_market_quote, recommend_market_quote,
)
from agencybill.tools.audit_tools import create_checkpoint
from agencybill.checkpoint import normalize_options


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
    from datetime import date, timedelta
    assert days_until((date.today() + timedelta(days=10)).isoformat()) == 10
    assert days_until(None) is None


def test_list_pending_decisions_ranked(tmp_db):
    wf_near = get_or_create_workflow("pol-1")
    # Make policy expire soon
    from agencybill.database import db
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=80)).isoformat()

    # Second policy/workflow farther out
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


def test_quote_enrichment_and_recommendation(tmp_db):
    wf = get_or_create_workflow("pol-1")
    sub = create_submission(wf["id"], "mkt-1")
    cheap = record_market_quote(
        submission_id=sub["id"], workflow_id=wf["id"], market_id="mkt-1",
        per_claim_limit=1000000, aggregate_limit=1000000, deductible=5000,
        annual_premium=20000, carrier="Cheap Co",
    )
    # Second quote via another submission
    from agencybill.database import db
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
                "quotes_waiting", "critical_alerts", "top_decisions", "generated_at"):
        assert key in brief
