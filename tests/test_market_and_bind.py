"""Market quote recommend/accept and binding persistence."""

import pytest

from agencybill.tools.policy_tools import get_or_create_workflow, read_policy
from agencybill.tools.market_tools import (
    create_submission, record_market_quote, recommend_market_quote,
    accept_market_quote, get_recommended_market_quote_id, get_submissions,
)
from agencybill.agents.binding import _bind_policy, _add_one_year
from agencybill.database import db, row_to_dict


def test_recommend_then_accept_does_not_bind_submission(tmp_db):
    wf = get_or_create_workflow("pol-1")
    sub = create_submission(wf["id"], "mkt-1", "test notes")
    mq = record_market_quote(
        submission_id=sub["id"],
        workflow_id=wf["id"],
        market_id="mkt-1",
        per_claim_limit=1000000,
        aggregate_limit=1000000,
        deductible=5000,
        annual_premium=22000,
        carrier="Test Carrier",
    )
    rec = recommend_market_quote(mq["id"], wf["id"], rationale="lowest premium")
    assert rec["status"] == "recommended"
    assert get_recommended_market_quote_id(wf["id"]) == mq["id"]

    accepted = accept_market_quote(mq["id"], wf["id"])
    assert accepted["status"] == "accepted"
    assert "quotes_id" in accepted

    subs = get_submissions(wf["id"])
    assert subs[0]["status"] == "quoted"  # not bound yet

    with db() as conn:
        q = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? AND status = 'accepted'",
            (wf["id"],),
        ).fetchone()
    assert q is not None
    assert q["annual_premium"] == 22000


def test_bind_policy_updates_policy_and_binds_submission(tmp_db):
    wf = get_or_create_workflow("pol-1")
    sub = create_submission(wf["id"], "mkt-1")
    mq = record_market_quote(
        submission_id=sub["id"], workflow_id=wf["id"], market_id="mkt-1",
        per_claim_limit=2000000, aggregate_limit=4000000, deductible=10000,
        annual_premium=30000, carrier="New Carrier",
    )
    recommend_market_quote(mq["id"], wf["id"])
    accepted = accept_market_quote(mq["id"], wf["id"])

    result = _bind_policy(wf["id"], "pol-1", accepted["quotes_id"])
    assert result["status"] == "bound"
    assert result["effective_date"] == "2026-01-01"
    assert result["expiration_date"] == "2027-01-01"
    assert result["annual_premium"] == 30000

    policy = read_policy("pol-1")
    assert policy["effective_date"] == "2026-01-01"
    assert policy["expiration_date"] == "2027-01-01"
    assert policy["annual_premium"] == 30000
    assert policy["limit_per_claim"] == 2000000

    subs = get_submissions(wf["id"])
    assert subs[0]["status"] == "bound"


@pytest.mark.parametrize("src,expected", [
    ("2024-02-29", "2025-02-28"),
    ("2023-02-28", "2024-02-28"),
    ("2025-01-15", "2026-01-15"),
    ("2024-12-31", "2025-12-31"),
])
def test_add_one_year_leap_safe(src, expected):
    assert _add_one_year(src) == expected
