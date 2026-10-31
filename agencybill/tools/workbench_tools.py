"""Account-manager workbench helpers: urgency ranking, decision context, morning brief."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agencybill.database import db, row_to_dict
from agencybill.tools.audit_tools import _enrich_checkpoint
from agencybill.logging_config import get_logger

log = get_logger("agencybill.workbench")

_PHASE_BOOST = {
    "BINDING": 20,
    "QUOTE_COLLECTION": 15,
    "REMITTANCE": 12,
    "INVOICING": 10,
    "UNDERWRITING": 8,
    "MARKET_SUBMISSION": 5,
    "APPLICATION": 4,
    "PRE_RENEWAL": 2,
}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def days_until(value: str | None, today: date | None = None) -> int | None:
    d = _parse_date(value)
    if not d:
        return None
    return (d - (today or date.today())).days


def urgency_score(
    *,
    days_to_expiry: int | None,
    checkpoint_age_days: float = 0,
    open_critical_alerts: int = 0,
    annual_premium: float | None = 0,
    phase: str = "",
) -> int:
    """Higher = decide sooner. Used to rank the AM approval queue."""
    score = 0
    if days_to_expiry is not None:
        if days_to_expiry < 0:
            score += 150
        elif days_to_expiry <= 7:
            score += 100
        elif days_to_expiry <= 14:
            score += 80
        elif days_to_expiry <= 30:
            score += 60
        elif days_to_expiry <= 60:
            score += 30
        elif days_to_expiry <= 90:
            score += 10

    score += min(int(checkpoint_age_days * 5), 40)
    score += max(0, open_critical_alerts) * 25
    score += _PHASE_BOOST.get(phase, 0)

    prem = float(annual_premium or 0)
    if prem >= 50000:
        score += 15
    elif prem >= 25000:
        score += 10
    elif prem >= 10000:
        score += 5
    return score


def urgency_band(score: int) -> str:
    if score >= 100:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def _checkpoint_age_days(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00").replace(" ", "T"))
        # SQLite datetime('now') is naive UTC-ish; compare naive
        if created.tzinfo:
            created = created.replace(tzinfo=None)
        return max(0.0, (datetime.now() - created).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _recommendation_for_workflow(conn, workflow_id: str, phase: str) -> dict[str, Any]:
    """Build a short AM-facing recommendation payload for the cockpit."""
    wf = conn.execute(
        "SELECT * FROM renewal_workflows WHERE id = ?", (workflow_id,)
    ).fetchone()
    if not wf:
        return {}

    rec: dict[str, Any] = {}
    rec_id = wf["recommended_market_quote_id"] if "recommended_market_quote_id" in wf.keys() else None

    if rec_id or phase == "QUOTE_COLLECTION":
        qid = rec_id
        if qid:
            row = conn.execute(
                """SELECT mq.*, m.name AS market_name
                   FROM market_quotes mq
                   JOIN markets m ON mq.market_id = m.id
                   WHERE mq.id = ?""",
                (qid,),
            ).fetchone()
            if row:
                q = row_to_dict(row)
                rec = {
                    "kind": "quote",
                    "title": f"Recommended: {q.get('market_name') or q.get('carrier') or 'Market'}",
                    "detail": (
                        f"{q.get('annual_premium', 0):,.0f}/yr · "
                        f"limits {q.get('per_claim_limit', 0):,.0f}/{q.get('aggregate_limit', 0):,.0f} · "
                        f"ded {q.get('deductible', 0):,.0f}"
                    ),
                    "market_quote_id": q["id"],
                    "annual_premium": q.get("annual_premium"),
                    "market_name": q.get("market_name"),
                    "carrier": q.get("carrier"),
                }

    if not rec and phase == "UNDERWRITING":
        row = conn.execute(
            """SELECT details_json FROM audit_events
               WHERE workflow_id = ? AND action = 'uw_decision'
               ORDER BY created_at DESC LIMIT 1""",
            (workflow_id,),
        ).fetchone()
        if row and row["details_json"]:
            import json
            try:
                details = json.loads(row["details_json"])
            except Exception:
                details = {}
            decision = details.get("decision", "eligible")
            score = details.get("risk_score")
            rec = {
                "kind": "underwriting",
                "title": f"UW recommendation: {decision}",
                "detail": f"Risk score {score}" if score is not None else details.get("notes", ""),
                "decision": decision,
                "risk_score": score,
            }

    if not rec and wf["notes"]:
        rec = {"kind": "notes", "title": "Workflow notes", "detail": str(wf["notes"])[:240]}

    return rec


def list_pending_decisions(ranked: bool = True) -> list[dict]:
    """Pending HITL checkpoints enriched for the AM cockpit, urgency-sorted."""
    with db() as conn:
        rows = conn.execute(
            """SELECT hc.*,
                      w.phase AS workflow_phase,
                      w.notes AS workflow_notes,
                      w.recommended_market_quote_id,
                      p.policy_number, p.expiration_date, p.annual_premium, p.carrier,
                      p.limit_per_claim, p.aggregate_limit, p.deductible,
                      f.name AS firm_name, f.state AS firm_state,
                      (SELECT COUNT(*) FROM alerts a
                         WHERE a.workflow_id = w.id AND a.status IN ('open','acknowledged')
                           AND a.severity = 'critical') AS critical_alerts,
                      (SELECT COUNT(*) FROM alerts a
                         WHERE a.workflow_id = w.id AND a.status IN ('open','acknowledged')) AS open_alerts
               FROM human_checkpoints hc
               JOIN renewal_workflows w ON hc.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE hc.decision IS NULL
               ORDER BY hc.created_at""",
        ).fetchall()

        results = []
        today = date.today()
        for row in rows:
            d = _enrich_checkpoint(row_to_dict(row))
            dte = days_until(d.get("expiration_date"), today)
            age = _checkpoint_age_days(d.get("created_at"))
            score = urgency_score(
                days_to_expiry=dte,
                checkpoint_age_days=age,
                open_critical_alerts=int(d.get("critical_alerts") or 0),
                annual_premium=d.get("annual_premium"),
                phase=d.get("phase") or d.get("workflow_phase") or "",
            )
            d["days_to_expiry"] = dte
            d["checkpoint_age_days"] = round(age, 1)
            d["urgency_score"] = score
            d["urgency_band"] = urgency_band(score)
            d["recommendation"] = _recommendation_for_workflow(
                conn, d["workflow_id"], d.get("phase") or ""
            )

            # Premium delta vs recommended quote
            rec = d["recommendation"]
            if rec.get("kind") == "quote" and d.get("annual_premium") and rec.get("annual_premium"):
                delta = float(rec["annual_premium"]) - float(d["annual_premium"])
                d["premium_delta"] = delta
                d["premium_delta_pct"] = round(delta / float(d["annual_premium"]) * 100, 1)
            else:
                d["premium_delta"] = None
                d["premium_delta_pct"] = None

            results.append(d)

    if ranked:
        results.sort(key=lambda x: (-x["urgency_score"], x.get("days_to_expiry") is None,
                                    x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 9999))
    return results


def enrich_market_quotes(workflow_id: str, expiring_premium: float | None = None) -> list[dict]:
    """Market quotes with comparison helpers for the quote workbench."""
    with db() as conn:
        rows = conn.execute(
            """SELECT mq.*, m.name AS market_name
               FROM market_quotes mq
               JOIN markets m ON mq.market_id = m.id
               WHERE mq.workflow_id = ?
               ORDER BY mq.annual_premium""",
            (workflow_id,),
        ).fetchall()
        wf = conn.execute(
            "SELECT recommended_market_quote_id, notes FROM renewal_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        recommended_id = wf["recommended_market_quote_id"] if wf else None
        if expiring_premium is None:
            pol = conn.execute(
                """SELECT p.annual_premium FROM policies p
                   JOIN renewal_workflows w ON w.policy_id = p.id
                   WHERE w.id = ?""",
                (workflow_id,),
            ).fetchone()
            expiring_premium = pol["annual_premium"] if pol else None

    quotes = []
    premiums = []
    for r in rows:
        q = row_to_dict(r)
        prem = float(q.get("annual_premium") or 0)
        premiums.append(prem)
        q["is_recommended"] = q["id"] == recommended_id or q.get("status") == "presented"
        if expiring_premium:
            q["delta_vs_expiring"] = prem - float(expiring_premium)
            q["delta_vs_expiring_pct"] = round(q["delta_vs_expiring"] / float(expiring_premium) * 100, 1)
        else:
            q["delta_vs_expiring"] = None
            q["delta_vs_expiring_pct"] = None
        quotes.append(q)

    if premiums:
        best = min(premiums)
        for q in quotes:
            q["is_best_premium"] = float(q.get("annual_premium") or 0) == best
    return quotes


def morning_brief() -> dict[str, Any]:
    """Portfolio pulse for the AM dashboard."""
    from datetime import timedelta
    today = date.today()
    d14 = (today + timedelta(days=14)).isoformat()
    d30 = (today + timedelta(days=30)).isoformat()
    today_s = today.isoformat()

    decisions = list_pending_decisions(ranked=True)

    with db() as conn:
        expiring_14 = conn.execute(
            """SELECT COUNT(*) AS n FROM policies
               WHERE status = 'active' AND expiration_date >= ? AND expiration_date <= ?""",
            (today_s, d14),
        ).fetchone()["n"]
        expiring_30 = conn.execute(
            """SELECT COUNT(*) AS n FROM policies
               WHERE status = 'active' AND expiration_date >= ? AND expiration_date <= ?""",
            (today_s, d30),
        ).fetchone()["n"]

        stale_apps = conn.execute(
            """SELECT COUNT(*) AS n FROM applications a
               JOIN renewal_workflows w ON a.workflow_id = w.id
               WHERE a.sent_at IS NOT NULL AND a.received_at IS NULL
                 AND w.status = 'active'
                 AND julianday('now') - julianday(a.sent_at) >= 10""",
        ).fetchone()["n"]

        quotes_waiting = conn.execute(
            """SELECT COUNT(*) AS n FROM market_submissions ms
               JOIN renewal_workflows w ON ms.workflow_id = w.id
               WHERE ms.status = 'submitted' AND w.status IN ('active','paused')
                 AND ms.follow_up_at IS NOT NULL AND ms.follow_up_at <= date('now')""",
        ).fetchone()["n"]

        critical_alerts = conn.execute(
            """SELECT COUNT(*) AS n FROM alerts
               WHERE status IN ('open','acknowledged') AND severity = 'critical'""",
        ).fetchone()["n"]

        remittances_due = conn.execute(
            """SELECT COUNT(*) AS n FROM remittances
               WHERE status = 'pending' AND due_date IS NOT NULL AND due_date <= date('now', '+7 days')""",
        ).fetchone()["n"]

        active_workflows = conn.execute(
            "SELECT COUNT(*) AS n FROM renewal_workflows WHERE status = 'active'"
        ).fetchone()["n"]

    top = decisions[:5]
    return {
        "decisions_waiting": len(decisions),
        "top_decisions": top,
        "expiring_14": expiring_14,
        "expiring_30": expiring_30,
        "stale_applications": stale_apps,
        "quotes_waiting": quotes_waiting,
        "critical_alerts": critical_alerts,
        "remittances_due": remittances_due,
        "active_workflows": active_workflows,
        "generated_at": datetime.now().strftime("%H:%M"),
    }
