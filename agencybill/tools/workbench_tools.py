"""Account-manager workbench helpers: urgency ranking, decision context, morning brief."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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

RESUME_HINTS = {
    "waiting for market quotes": ("Resume quote collection", "Markets still need to respond or quotes need recording."),
    "missing recommended quote": ("Resume quote collection", "Pick or recommend a market quote, then continue."),
    "quote selection review": ("Resume quote selection", "Revise the recommended quote, then re-run."),
    "awaiting premium payment": ("Resume after payment", "Record premium payment, then continue to remittance."),
    "pre-checkpoint": ("Resume binding / remittance", "Confirm the pre-phase checkpoint to continue."),
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
        if created.tzinfo:
            created = created.replace(tzinfo=None)
        return max(0.0, (datetime.now() - created).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _risk_chips(conn, firm_id: str, workflow_id: str, phase: str) -> dict[str, Any]:
    """Compact claims + UW risk summary for cockpit chips."""
    cutoff = f"{date.today().year - 5}-01-01"
    row = conn.execute(
        """SELECT
              COUNT(*) AS total_claims,
              SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_claims,
              COALESCE(SUM(amount_paid), 0) + COALESCE(SUM(amount_reserved), 0) AS total_incurred
           FROM claims
           WHERE firm_id = ? AND claim_date >= ?""",
        (firm_id, cutoff),
    ).fetchone()
    chips = {
        "total_claims": int(row["total_claims"] or 0) if row else 0,
        "open_claims": int(row["open_claims"] or 0) if row else 0,
        "total_incurred": float(row["total_incurred"] or 0) if row else 0.0,
        "risk_score": None,
        "uw_decision": None,
    }
    uw = conn.execute(
        """SELECT details_json FROM audit_events
           WHERE workflow_id = ? AND action = 'uw_decision'
           ORDER BY created_at DESC LIMIT 1""",
        (workflow_id,),
    ).fetchone()
    if uw and uw["details_json"]:
        try:
            details = json.loads(uw["details_json"])
            chips["risk_score"] = details.get("risk_score")
            chips["uw_decision"] = details.get("decision")
        except Exception:
            pass
    return chips


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
                        f"${q.get('annual_premium', 0):,.0f}/yr · "
                        f"limits ${q.get('per_claim_limit', 0):,.0f}/${q.get('aggregate_limit', 0):,.0f} · "
                        f"ded ${q.get('deductible', 0):,.0f}"
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


def _alternate_quotes(conn, workflow_id: str, recommended_id: str | None,
                      expiring_premium: float | None) -> list[dict]:
    rows = conn.execute(
        """SELECT mq.*, m.name AS market_name
           FROM market_quotes mq
           JOIN markets m ON mq.market_id = m.id
           WHERE mq.workflow_id = ? AND mq.status != 'declined'
           ORDER BY mq.annual_premium""",
        (workflow_id,),
    ).fetchall()
    out = []
    for r in rows:
        q = row_to_dict(r)
        prem = float(q.get("annual_premium") or 0)
        q["is_recommended"] = q["id"] == recommended_id
        if expiring_premium:
            q["delta_vs_expiring"] = prem - float(expiring_premium)
            q["delta_vs_expiring_pct"] = round(
                q["delta_vs_expiring"] / float(expiring_premium) * 100, 1
            )
        else:
            q["delta_vs_expiring"] = None
            q["delta_vs_expiring_pct"] = None
        out.append(q)
    return out


def list_pending_decisions(ranked: bool = True) -> list[dict]:
    """Pending HITL checkpoints enriched for the AM cockpit, urgency-sorted."""
    with db() as conn:
        rows = conn.execute(
            """SELECT hc.*,
                      w.phase AS workflow_phase,
                      w.notes AS workflow_notes,
                      w.recommended_market_quote_id,
                      w.policy_id,
                      p.policy_number, p.expiration_date, p.annual_premium, p.carrier,
                      p.limit_per_claim, p.aggregate_limit, p.deductible,
                      p.firm_id,
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
            phase = d.get("phase") or d.get("workflow_phase") or ""
            dte = days_until(d.get("expiration_date"), today)
            age = _checkpoint_age_days(d.get("created_at"))
            score = urgency_score(
                days_to_expiry=dte,
                checkpoint_age_days=age,
                open_critical_alerts=int(d.get("critical_alerts") or 0),
                annual_premium=d.get("annual_premium"),
                phase=phase,
            )
            d["days_to_expiry"] = dte
            d["checkpoint_age_days"] = round(age, 1)
            d["urgency_score"] = score
            d["urgency_band"] = urgency_band(score)
            d["recommendation"] = _recommendation_for_workflow(conn, d["workflow_id"], phase)
            d["risk"] = _risk_chips(conn, d.get("firm_id", ""), d["workflow_id"], phase)

            rec = d["recommendation"]
            if rec.get("kind") == "quote" and d.get("annual_premium") and rec.get("annual_premium"):
                delta = float(rec["annual_premium"]) - float(d["annual_premium"])
                d["premium_delta"] = delta
                d["premium_delta_pct"] = round(delta / float(d["annual_premium"]) * 100, 1)
            else:
                d["premium_delta"] = None
                d["premium_delta_pct"] = None

            if phase == "QUOTE_COLLECTION":
                d["alternate_quotes"] = _alternate_quotes(
                    conn, d["workflow_id"],
                    d.get("recommended_market_quote_id") or rec.get("market_quote_id"),
                    d.get("annual_premium"),
                )
            else:
                d["alternate_quotes"] = []

            results.append(d)

    if ranked:
        results.sort(
            key=lambda x: (
                -x["urgency_score"],
                x.get("days_to_expiry") is None,
                x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 9999,
            )
        )
    return results


def next_pending_after(checkpoint_id: str, band: str = "") -> dict | None:
    """Return the next ranked pending decision after resolving checkpoint_id."""
    ranked = list_pending_decisions(ranked=True)
    if band:
        ranked = [c for c in ranked if c.get("urgency_band") == band]
    # resolved checkpoint is already gone from pending list
    return ranked[0] if ranked else None


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
            q["delta_vs_expiring_pct"] = round(
                q["delta_vs_expiring"] / float(expiring_premium) * 100, 1
            )
        else:
            q["delta_vs_expiring"] = None
            q["delta_vs_expiring_pct"] = None
        quotes.append(q)

    if premiums:
        best = min(premiums)
        for q in quotes:
            q["is_best_premium"] = float(q.get("annual_premium") or 0) == best
    return quotes


def pause_context(workflow: dict) -> dict[str, str]:
    """Human-readable pause reason + resume CTA for paused workflows."""
    notes = (workflow.get("notes") or "").strip()
    phase = workflow.get("phase") or ""
    status = workflow.get("status") or ""
    if status != "paused":
        return {"reason": notes, "resume_label": "Run / Resume", "hint": ""}

    lower = notes.lower()
    for needle, (label, hint) in RESUME_HINTS.items():
        if needle in lower:
            return {"reason": notes or needle, "resume_label": label, "hint": hint}

    phase_labels = {
        "QUOTE_COLLECTION": ("Resume quote collection", "Continue collecting or selecting market quotes."),
        "INVOICING": ("Resume invoicing", "Confirm payment status to continue."),
        "BINDING": ("Resume binding", "Confirm bind to continue."),
        "REMITTANCE": ("Resume remittance", "Confirm remittance to continue."),
        "UNDERWRITING": ("Resume underwriting", "Complete the underwriting decision."),
        "MARKET_SUBMISSION": ("Resume market submission", "Continue market outreach."),
        "APPLICATION": ("Resume application", "Continue the renewal application."),
    }
    label, hint = phase_labels.get(phase, (f"Resume {phase.replace('_', ' ').title()}", notes))
    return {"reason": notes or f"Paused in {phase}", "resume_label": label, "hint": hint}


def get_worklist(kind: str) -> dict[str, Any]:
    """Filtered AM worklists backing morning-brief deep links."""
    today = date.today()
    today_s = today.isoformat()
    d14 = (today + timedelta(days=14)).isoformat()
    d30 = (today + timedelta(days=30)).isoformat()

    with db() as conn:
        if kind in ("expiring_14", "expiring_30"):
            end = d14 if kind == "expiring_14" else d30
            title = f"Policies expiring within {'14' if kind == 'expiring_14' else '30'} days"
            rows = conn.execute(
                """SELECT p.*, f.name AS firm_name, f.state AS firm_state,
                          (SELECT w.id FROM renewal_workflows w
                             WHERE w.policy_id = p.id AND w.status IN ('active','paused')
                             ORDER BY w.started_at DESC LIMIT 1) AS workflow_id
                   FROM policies p
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE p.status = 'active'
                     AND p.expiration_date >= ? AND p.expiration_date <= ?
                   ORDER BY p.expiration_date""",
                (today_s, end),
            ).fetchall()
            entries = []
            for r in rows:
                d = row_to_dict(r)
                d["days_to_expiry"] = days_until(d.get("expiration_date"), today)
                d["href"] = (
                    f"/workflows/{d['workflow_id']}" if d.get("workflow_id")
                    else "/policies"
                )
                d["detail"] = f"Expires {d.get('expiration_date','')[:10]} · {d.get('carrier','')}"
                entries.append(d)
            return {"kind": kind, "title": title, "entries": entries}

        if kind == "stale_apps":
            rows = conn.execute(
                """SELECT a.*, w.id AS workflow_id, w.phase, w.status AS wf_status,
                          p.policy_number, f.name AS firm_name,
                          CAST(julianday('now') - julianday(a.sent_at) AS INT) AS days_waiting
                   FROM applications a
                   JOIN renewal_workflows w ON a.workflow_id = w.id
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE a.sent_at IS NOT NULL AND a.received_at IS NULL
                     AND w.status IN ('active','paused')
                     AND julianday('now') - julianday(a.sent_at) >= 10
                   ORDER BY a.sent_at""",
            ).fetchall()
            entries = []
            for r in rows:
                d = row_to_dict(r)
                d["href"] = f"/workflows/{d['workflow_id']}"
                d["detail"] = f"App outstanding {d.get('days_waiting')}d · phase {d.get('phase')}"
                entries.append(d)
            return {"kind": kind, "title": "Stale renewal applications (≥10 days)", "entries": entries}

        if kind == "market_followups":
            rows = conn.execute(
                """SELECT ms.*, m.name AS market_name, w.id AS workflow_id, w.phase,
                          p.policy_number, f.name AS firm_name
                   FROM market_submissions ms
                   JOIN markets m ON ms.market_id = m.id
                   JOIN renewal_workflows w ON ms.workflow_id = w.id
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE ms.status = 'submitted' AND w.status IN ('active','paused')
                     AND ms.follow_up_at IS NOT NULL AND ms.follow_up_at <= date('now')
                   ORDER BY ms.follow_up_at""",
            ).fetchall()
            entries = []
            for r in rows:
                d = row_to_dict(r)
                d["href"] = f"/workflows/{d['workflow_id']}"
                d["detail"] = (
                    f"{d.get('market_name')} · follow-up was {d.get('follow_up_at','')[:10]}"
                )
                entries.append(d)
            return {"kind": kind, "title": "Market follow-ups due", "entries": entries}

        if kind == "remittances_due":
            rows = conn.execute(
                """SELECT r.*, w.id AS workflow_id, p.policy_number, f.name AS firm_name
                   FROM remittances r
                   JOIN renewal_workflows w ON r.workflow_id = w.id
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE r.status = 'pending'
                     AND r.due_date IS NOT NULL
                     AND r.due_date <= date('now', '+7 days')
                   ORDER BY r.due_date""",
            ).fetchall()
            entries = []
            for r in rows:
                d = row_to_dict(r)
                d["href"] = f"/workflows/{d['workflow_id']}"
                d["detail"] = (
                    f"{d.get('carrier')} · ${d.get('amount', 0):,.0f} due {d.get('due_date','')[:10]}"
                )
                entries.append(d)
            return {"kind": kind, "title": "Remittances due within 7 days", "entries": entries}

        if kind == "paused":
            rows = conn.execute(
                """SELECT w.*, p.policy_number, p.expiration_date, f.name AS firm_name
                   FROM renewal_workflows w
                   JOIN policies p ON w.policy_id = p.id
                   JOIN law_firms f ON p.firm_id = f.id
                   WHERE w.status = 'paused'
                   ORDER BY w.updated_at DESC""",
            ).fetchall()
            entries = []
            for r in rows:
                d = row_to_dict(r)
                ctx = pause_context(d)
                d["href"] = f"/workflows/{d['id']}"
                d["detail"] = ctx["reason"]
                d["resume_label"] = ctx["resume_label"]
                entries.append(d)
            return {"kind": kind, "title": "Paused workflows", "entries": entries}

    return {"kind": kind, "title": "Unknown worklist", "entries": []}


def morning_brief() -> dict[str, Any]:
    """Portfolio pulse for the AM dashboard."""
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
                 AND w.status IN ('active','paused')
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
               WHERE status = 'pending' AND due_date IS NOT NULL
                 AND due_date <= date('now', '+7 days')""",
        ).fetchone()["n"]

        paused = conn.execute(
            "SELECT COUNT(*) AS n FROM renewal_workflows WHERE status = 'paused'"
        ).fetchone()["n"]

        active_workflows = conn.execute(
            "SELECT COUNT(*) AS n FROM renewal_workflows WHERE status = 'active'"
        ).fetchone()["n"]

    return {
        "decisions_waiting": len(decisions),
        "top_decisions": decisions[:5],
        "expiring_14": expiring_14,
        "expiring_30": expiring_30,
        "stale_applications": stale_apps,
        "quotes_waiting": quotes_waiting,
        "critical_alerts": critical_alerts,
        "remittances_due": remittances_due,
        "paused": paused,
        "active_workflows": active_workflows,
        "generated_at": datetime.now().strftime("%H:%M"),
    }
