"""Alert scanning and management tools.

Scans the database for exception conditions across all workflow phases and
creates structured alert records. Designed to be called periodically by the
background worker. Uses dedup_key to avoid duplicate alerts for the same
active condition.
"""

import uuid
from datetime import datetime, date, timedelta

from agencybill.database import db, row_to_dict


# ─────────────────────────────────────────────────────────────────────────────
# THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

_POLICY_EXPIRY_CRITICAL_DAYS = 30   # policy expires this soon with no bound renewal
_POLICY_EXPIRY_WARNING_DAYS  = 60   # advance warning
_QUOTE_EXPIRY_WARNING_HOURS  = 48   # accepted/received quote valid_through within this window
_MARKET_FOLLOWUP_GRACE_DAYS  = 1    # days past follow_up_at before alert fires
_APPLICATION_OVERDUE_DAYS    = 10   # days after sent_at with no received_at
_CHECKPOINT_STALE_DAYS       = 3    # days a checkpoint can sit undecided before alert
_WORKFLOW_STALL_DAYS         = 5    # days without phase advancement
_INVOICE_GRACE_DAYS          = 0    # days past due_date before overdue alert
_REMITTANCE_GRACE_DAYS       = 0


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_alert(
    alert_type: str,
    severity: str,
    title: str,
    detail: str,
    dedup_key: str,
    workflow_id: str = None,
    entity_type: str = None,
    entity_id: str = None,
) -> None:
    """Insert alert if dedup_key not already present (open or acknowledged)."""
    with db() as conn:
        existing = conn.execute(
            "SELECT id, status FROM alerts WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        if existing:
            # Refresh updated_at so UI can show "still active"
            conn.execute(
                "UPDATE alerts SET updated_at = datetime('now') WHERE dedup_key = ?",
                (dedup_key,),
            )
            return
        conn.execute(
            """INSERT INTO alerts
               (id, alert_type, severity, workflow_id, entity_type, entity_id,
                title, detail, dedup_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), alert_type, severity, workflow_id,
             entity_type, entity_id, title, detail, dedup_key),
        )


def _auto_resolve(dedup_key: str) -> None:
    """Mark an alert resolved when its condition clears."""
    with db() as conn:
        conn.execute(
            """UPDATE alerts
               SET status = 'resolved', resolved_at = datetime('now'),
                   resolved_by = 'system'
               WHERE dedup_key = ? AND status IN ('open','acknowledged')""",
            (dedup_key,),
        )


def _today() -> date:
    return date.today()


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()


def _days_from_now(d: str | None, n: int) -> bool:
    """Return True if date string d is within n days from today (inclusive)."""
    if not d:
        return False
    try:
        target = date.fromisoformat(d[:10])
        return _today() <= target <= _today() + timedelta(days=n)
    except ValueError:
        return False


def _is_past(d: str | None, grace_days: int = 0) -> bool:
    """Return True if date string d has passed (plus grace period)."""
    if not d:
        return False
    try:
        target = date.fromisoformat(d[:10]) + timedelta(days=grace_days)
        return _today() > target
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL SCANNERS
# ─────────────────────────────────────────────────────────────────────────────

def _scan_policy_expiry() -> None:
    """Alert when a policy is expiring soon without a bound/complete renewal."""
    with db() as conn:
        rows = conn.execute(
            """SELECT p.id AS policy_id, p.policy_number, p.expiration_date,
                      f.name AS firm_name,
                      w.id AS workflow_id, w.phase, w.status AS wf_status
               FROM policies p
               JOIN law_firms f ON p.firm_id = f.id
               LEFT JOIN renewal_workflows w ON w.policy_id = p.id
                   AND w.status NOT IN ('complete','non_renewal','declined','cancelled')
               WHERE p.status = 'active'"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        exp = r.get("expiration_date", "")
        if not exp:
            continue
        bound_phases = {"BINDING", "INVOICING", "REMITTANCE", "COMPLETE"}
        is_bound = r.get("phase") in bound_phases
        key_c = f"policy_expiry_critical:{r['policy_id']}"
        key_w = f"policy_expiry_warning:{r['policy_id']}"

        if _days_from_now(exp, _POLICY_EXPIRY_CRITICAL_DAYS) and not is_bound:
            _upsert_alert(
                "policy_expiring_unbound", "critical",
                f"Policy expiring in ≤{_POLICY_EXPIRY_CRITICAL_DAYS} days — not yet bound",
                f"{r['firm_name']} · {r['policy_number']} expires {exp[:10]}. "
                f"Renewal is at phase {r.get('phase') or 'not started'}.",
                key_c,
                workflow_id=r.get("workflow_id"),
                entity_type="policy", entity_id=r["policy_id"],
            )
            _auto_resolve(key_w)
        elif _days_from_now(exp, _POLICY_EXPIRY_WARNING_DAYS) and not is_bound:
            _upsert_alert(
                "policy_expiry_warning", "warning",
                f"Policy expiring in ≤{_POLICY_EXPIRY_WARNING_DAYS} days",
                f"{r['firm_name']} · {r['policy_number']} expires {exp[:10]}. "
                f"Renewal phase: {r.get('phase') or 'not started'}.",
                key_w,
                workflow_id=r.get("workflow_id"),
                entity_type="policy", entity_id=r["policy_id"],
            )
        else:
            _auto_resolve(key_c)
            _auto_resolve(key_w)


def _scan_market_followup() -> None:
    """Alert on market submissions past their follow-up date with no response."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ms.id, ms.workflow_id, ms.follow_up_at, ms.submitted_at,
                      m.name AS market_name, m.contact_email,
                      f.name AS firm_name
               FROM market_submissions ms
               JOIN markets m ON ms.market_id = m.id
               JOIN renewal_workflows w ON ms.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE ms.status = 'submitted'"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"market_followup_overdue:{r['id']}"
        if _is_past(r.get("follow_up_at"), _MARKET_FOLLOWUP_GRACE_DAYS):
            _upsert_alert(
                "market_followup_overdue", "warning",
                f"No quote from {r['market_name']} — follow-up overdue",
                f"{r['firm_name']}: submission to {r['market_name']} sent "
                f"{r.get('submitted_at','')[:10]}, follow-up was due "
                f"{r.get('follow_up_at','')[:10]}. Contact: {r.get('contact_email','')}.",
                key,
                workflow_id=r["workflow_id"],
                entity_type="submission", entity_id=r["id"],
            )
        else:
            _auto_resolve(key)


def _scan_all_markets_declined() -> None:
    """Alert when every market for a workflow has declined or gone silent."""
    with db() as conn:
        rows = conn.execute(
            """SELECT w.id AS workflow_id, w.phase, f.name AS firm_name,
                      COUNT(ms.id) AS total,
                      SUM(CASE WHEN ms.status IN ('declined','no_response','passed')
                               THEN 1 ELSE 0 END) AS negative
               FROM renewal_workflows w
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               JOIN market_submissions ms ON ms.workflow_id = w.id
               WHERE w.phase IN ('MARKET_SUBMISSION','QUOTE_COLLECTION')
                 AND w.status = 'active'
               GROUP BY w.id"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"all_markets_declined:{r['workflow_id']}"
        if r["total"] > 0 and r["total"] == r["negative"]:
            _upsert_alert(
                "all_markets_declined", "critical",
                "All markets declined or no response — placement at risk",
                f"{r['firm_name']}: {r['total']} market(s) submitted, all have "
                f"declined or not responded. Need additional market outlets.",
                key,
                workflow_id=r["workflow_id"],
                entity_type="workflow", entity_id=r["workflow_id"],
            )
        else:
            _auto_resolve(key)


def _scan_quote_expiry() -> None:
    """Alert when an accepted or received market quote is about to expire."""
    cutoff = (datetime.now() + timedelta(hours=_QUOTE_EXPIRY_WARNING_HOURS)).isoformat()
    with db() as conn:
        rows = conn.execute(
            """SELECT mq.id, mq.workflow_id, mq.valid_through, mq.annual_premium,
                      m.name AS market_name, f.name AS firm_name
               FROM market_quotes mq
               JOIN markets m ON mq.market_id = m.id
               JOIN renewal_workflows w ON mq.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE mq.status IN ('received','accepted')
                 AND mq.valid_through IS NOT NULL
                 AND mq.valid_through != ''
                 AND mq.valid_through <= ?""",
            (cutoff,),
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"quote_expiring:{r['id']}"
        _upsert_alert(
            "quote_expiring", "critical",
            f"Quote from {r['market_name']} expires soon",
            f"{r['firm_name']}: quote of ${r.get('annual_premium', 0):,.0f}/yr "
            f"valid through {r.get('valid_through','')[:10]}. "
            "Bind or request extension immediately.",
            key,
            workflow_id=r["workflow_id"],
            entity_type="quote", entity_id=r["id"],
        )


def _scan_application_overdue() -> None:
    """Alert when a renewal application has been sent but not returned."""
    cutoff = _days_ago(_APPLICATION_OVERDUE_DAYS)
    with db() as conn:
        rows = conn.execute(
            """SELECT a.id, a.workflow_id, a.sent_at, f.name AS firm_name, f.email AS firm_email
               FROM applications a
               JOIN renewal_workflows w ON a.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE a.completed = 0
                 AND a.sent_at IS NOT NULL
                 AND a.sent_at < ?
                 AND w.status = 'active'""",
            (cutoff,),
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"application_overdue:{r['id']}"
        _upsert_alert(
            "application_not_returned", "warning",
            f"Renewal application not returned — {r['firm_name']}",
            f"Application sent {r.get('sent_at','')[:10]}, more than "
            f"{_APPLICATION_OVERDUE_DAYS} days ago. "
            f"Follow up with insured ({r.get('firm_email','')}) to collect completed application.",
            key,
            workflow_id=r["workflow_id"],
            entity_type="workflow", entity_id=r["workflow_id"],
        )


def _scan_stale_checkpoints() -> None:
    """Alert on human checkpoints that have been waiting too long for a decision."""
    cutoff = _days_ago(_CHECKPOINT_STALE_DAYS)
    with db() as conn:
        rows = conn.execute(
            """SELECT hc.id, hc.workflow_id, hc.phase, hc.prompt, hc.created_at,
                      f.name AS firm_name
               FROM human_checkpoints hc
               JOIN renewal_workflows w ON hc.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE hc.decision IS NULL
                 AND hc.created_at < ?""",
            (cutoff,),
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"checkpoint_stale:{r['id']}"
        _upsert_alert(
            "checkpoint_stale", "warning",
            f"Approval pending {_CHECKPOINT_STALE_DAYS}+ days — {r['phase']}",
            f"{r['firm_name']}: checkpoint created {r.get('created_at','')[:10]} "
            f"at phase {r['phase']} is awaiting a decision. "
            f"Prompt: \"{r.get('prompt','')[:120]}\"",
            key,
            workflow_id=r["workflow_id"],
            entity_type="workflow", entity_id=r["workflow_id"],
        )


def _scan_stalled_workflows() -> None:
    """Alert on active workflows that haven't advanced phase in too long."""
    cutoff = _days_ago(_WORKFLOW_STALL_DAYS)
    terminal = ('complete', 'non_renewal', 'declined', 'cancelled', 'paused')
    with db() as conn:
        rows = conn.execute(
            """SELECT w.id, w.phase, w.updated_at, f.name AS firm_name
               FROM renewal_workflows w
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE w.status = 'active'
                 AND w.updated_at < ?""",
            (cutoff,),
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"workflow_stalled:{r['id']}"
        _upsert_alert(
            "workflow_stalled", "warning",
            f"Workflow stalled at {r['phase']} for {_WORKFLOW_STALL_DAYS}+ days",
            f"{r['firm_name']}: workflow has not advanced since "
            f"{r.get('updated_at','')[:10]}. Check for blocking issues or errors.",
            key,
            workflow_id=r["id"],
            entity_type="workflow", entity_id=r["id"],
        )


def _scan_invoice_overdue() -> None:
    """Alert on unpaid invoices past their due date."""
    with db() as conn:
        rows = conn.execute(
            """SELECT inv.id, inv.workflow_id, inv.invoice_number, inv.amount_due,
                      inv.due_date, inv.status, f.name AS firm_name
               FROM invoices inv
               JOIN renewal_workflows w ON inv.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE inv.status IN ('unpaid','partial')"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"invoice_overdue:{r['id']}"
        if _is_past(r.get("due_date"), _INVOICE_GRACE_DAYS):
            _upsert_alert(
                "invoice_overdue", "critical",
                f"Invoice overdue — {r['firm_name']}",
                f"Invoice {r['invoice_number']} for ${r.get('amount_due', 0):,.2f} "
                f"was due {r.get('due_date','')[:10]}. Status: {r['status']}. "
                "Follow up for payment or initiate cancellation process.",
                key,
                workflow_id=r["workflow_id"],
                entity_type="invoice", entity_id=r["id"],
            )
            # Mark the invoice record itself as overdue
            with db() as conn:
                conn.execute(
                    "UPDATE invoices SET status = 'overdue' WHERE id = ? AND status = 'unpaid'",
                    (r["id"],),
                )
        else:
            _auto_resolve(key)


def _scan_remittance_overdue() -> None:
    """Alert on carrier remittances past their due date."""
    with db() as conn:
        rows = conn.execute(
            """SELECT r.id, r.workflow_id, r.carrier, r.amount, r.due_date,
                      f.name AS firm_name
               FROM remittances r
               JOIN renewal_workflows w ON r.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE r.status = 'pending'"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"remittance_overdue:{r['id']}"
        if _is_past(r.get("due_date"), _REMITTANCE_GRACE_DAYS):
            _upsert_alert(
                "remittance_overdue", "warning",
                f"Carrier remittance overdue — {r['carrier']}",
                f"{r['firm_name']}: remittance of ${r.get('amount', 0):,.2f} "
                f"to {r['carrier']} was due {r.get('due_date','')[:10]}. "
                "Initiate wire/ACH transfer and mark remitted.",
                key,
                workflow_id=r["workflow_id"],
                entity_type="workflow", entity_id=r["workflow_id"],
            )
            with db() as conn:
                conn.execute(
                    "UPDATE remittances SET status = 'overdue' WHERE id = ? AND status = 'pending'",
                    (r["id"],),
                )
        else:
            _auto_resolve(key)


def _scan_invoice_short_pay() -> None:
    """Alert when total payments received are less than the invoice amount."""
    with db() as conn:
        rows = conn.execute(
            """SELECT inv.id, inv.workflow_id, inv.invoice_number,
                      inv.amount_due, COALESCE(SUM(pay.amount), 0) AS paid,
                      f.name AS firm_name
               FROM invoices inv
               LEFT JOIN payments pay ON pay.invoice_id = inv.id
               JOIN renewal_workflows w ON inv.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE inv.status NOT IN ('void','paid')
               GROUP BY inv.id
               HAVING paid > 0 AND paid < inv.amount_due"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"invoice_short_pay:{r['id']}"
        shortage = r["amount_due"] - r["paid"]
        _upsert_alert(
            "invoice_short_pay", "warning",
            f"Short payment on invoice {r['invoice_number']}",
            f"{r['firm_name']}: received ${r['paid']:,.2f} of "
            f"${r['amount_due']:,.2f} due. "
            f"Remaining balance: ${shortage:,.2f}. Follow up for balance.",
            key,
            workflow_id=r["workflow_id"],
            entity_type="invoice", entity_id=r["id"],
        )


def _scan_claims_during_renewal() -> None:
    """Alert when a new claim is filed against a firm with an active renewal."""
    with db() as conn:
        rows = conn.execute(
            """SELECT c.id AS claim_id, c.claim_date, c.claim_type, c.amount_reserved,
                      c.description, c.status AS claim_status,
                      w.id AS workflow_id, w.phase,
                      f.name AS firm_name
               FROM claims c
               JOIN renewal_workflows w ON w.policy_id = c.policy_id
               JOIN law_firms f ON c.firm_id = f.id
               WHERE w.status = 'active'
                 AND c.created_at >= w.started_at"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"claim_during_renewal:{r['claim_id']}:{r['workflow_id']}"
        _upsert_alert(
            "claim_during_renewal", "warning",
            f"New claim filed during active renewal — {r['firm_name']}",
            f"A {r.get('claim_type','') or 'new'} claim was filed on "
            f"{r.get('claim_date','')[:10]} while renewal is at phase {r['phase']}. "
            f"Reserved: ${r.get('amount_reserved', 0):,.0f}. "
            "Notify underwriter — this may affect quoting or binding.",
            key,
            workflow_id=r["workflow_id"],
            entity_type="claim", entity_id=r["claim_id"],
        )


def _scan_agent_errors() -> None:
    """Alert on phase errors logged in the audit trail since last scan."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ae.id, ae.workflow_id, ae.details_json, ae.created_at,
                      f.name AS firm_name
               FROM audit_events ae
               JOIN renewal_workflows w ON ae.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE ae.action = 'phase_error'"""
        ).fetchall()

    for r in [row_to_dict(x) for x in rows]:
        key = f"agent_error:{r['id']}"
        import json
        try:
            details = json.loads(r.get("details_json") or "{}")
        except Exception:
            details = {}
        _upsert_alert(
            "agent_error", "critical",
            f"Agent error — {details.get('phase','unknown phase')}",
            f"{r['firm_name']}: phase {details.get('phase','')} failed at "
            f"{r.get('created_at','')[:16]}. "
            f"Error: {str(details.get('error',''))[:200]}",
            key,
            workflow_id=r["workflow_id"],
            entity_type="workflow", entity_id=r["workflow_id"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def run_alert_scan() -> int:
    """Run all scanners. Returns the number of open alerts after the scan."""
    _scan_policy_expiry()
    _scan_market_followup()
    _scan_all_markets_declined()
    _scan_quote_expiry()
    _scan_application_overdue()
    _scan_stale_checkpoints()
    _scan_stalled_workflows()
    _scan_invoice_overdue()
    _scan_remittance_overdue()
    _scan_invoice_short_pay()
    _scan_claims_during_renewal()
    _scan_agent_errors()
    return count_open_alerts()


def get_alerts(
    status: str = "open",
    severity: str = None,
    workflow_id: str = None,
    limit: int = 200,
) -> list[dict]:
    """Return alerts filtered by status, severity, or workflow."""
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if workflow_id:
        clauses.append("workflow_id = ?")
        params.append(workflow_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM alerts {where} ORDER BY "
            f"CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
            f"created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def count_open_alerts(severity: str = None) -> int:
    """Count open (+ acknowledged) alerts, optionally filtered by severity."""
    with db() as conn:
        if severity:
            row = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE status IN ('open','acknowledged') AND severity = ?",
                (severity,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE status IN ('open','acknowledged')"
            ).fetchone()
    return row[0] if row else 0


def acknowledge_alert(alert_id: str, acknowledged_by: str = "user") -> None:
    with db() as conn:
        conn.execute(
            """UPDATE alerts SET status = 'acknowledged',
               acknowledged_at = datetime('now'), acknowledged_by = ?
               WHERE id = ?""",
            (acknowledged_by, alert_id),
        )


def resolve_alert(alert_id: str, resolved_by: str = "user") -> None:
    with db() as conn:
        conn.execute(
            """UPDATE alerts SET status = 'resolved',
               resolved_at = datetime('now'), resolved_by = ?
               WHERE id = ?""",
            (resolved_by, alert_id),
        )


def get_alerts_for_workflow(workflow_id: str) -> list[dict]:
    return get_alerts(status=None, workflow_id=workflow_id)
