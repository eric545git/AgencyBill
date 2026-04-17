"""Tools for market submissions and tracking quotes from wholesalers/program admins."""

import uuid
import json
from datetime import datetime, date

from agencybill.database import db, row_to_dict


# ─────────────────────────────────────────────────────────────────────────────
# MARKETS DIRECTORY
# ─────────────────────────────────────────────────────────────────────────────

def list_markets(active_only: bool = True) -> list[dict]:
    """Return all market records."""
    with db() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM markets WHERE active = 1 ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM markets ORDER BY name").fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        if d.get("states"):
            try:
                d["states_list"] = json.loads(d["states"])
            except Exception:
                d["states_list"] = []
        else:
            d["states_list"] = []
        result.append(d)
    return result


def get_market(market_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM markets WHERE id = ?", (market_id,)).fetchone()
    return row_to_dict(row)


def select_markets_for_account(
    attorney_count: int,
    state: str,
    firm_type: str,
    practice_areas: list[str],
    estimated_premium: float = 0,
) -> list[dict]:
    """
    Filter the market directory to find appropriate markets for a submission.
    Returns markets that match the account profile (attorney count, state, premium range).
    """
    all_markets = list_markets(active_only=True)
    eligible = []
    for m in all_markets:
        # Attorney count filter
        min_atty = m.get("min_attorneys") or 0
        max_atty = m.get("max_attorneys")
        if attorney_count < min_atty:
            continue
        if max_atty and attorney_count > max_atty:
            continue

        # State filter
        states_list = m.get("states_list", [])
        if states_list and state.upper() not in [s.upper() for s in states_list]:
            continue

        # Premium range filter
        min_prem = m.get("min_premium") or 0
        max_prem = m.get("max_premium")
        if estimated_premium > 0:
            if estimated_premium < min_prem:
                continue
            if max_prem and estimated_premium > max_prem:
                continue

        eligible.append(m)
    return eligible


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SUBMISSIONS
# ─────────────────────────────────────────────────────────────────────────────

def create_submission(workflow_id: str, market_id: str,
                       submission_notes: str = "") -> dict:
    """Create a market submission record."""
    sub_id = str(uuid.uuid4())
    follow_up = _days_from_now(5)
    with db() as conn:
        conn.execute(
            """INSERT INTO market_submissions
               (id, workflow_id, market_id, submission_notes, follow_up_at)
               VALUES (?, ?, ?, ?, ?)""",
            (sub_id, workflow_id, market_id, submission_notes, follow_up),
        )
    market = get_market(market_id)
    return {
        "id": sub_id,
        "workflow_id": workflow_id,
        "market_id": market_id,
        "market_name": market.get("name"),
        "contact_email": market.get("contact_email"),
        "status": "submitted",
        "follow_up_at": follow_up,
    }


def get_submissions(workflow_id: str) -> list[dict]:
    """Return all market submissions for a workflow, joined with market info."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ms.*, m.name AS market_name, m.contact_name,
                      m.contact_email, m.specialty
               FROM market_submissions ms
               JOIN markets m ON ms.market_id = m.id
               WHERE ms.workflow_id = ?
               ORDER BY ms.submitted_at""",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def update_submission_status(submission_id: str, status: str,
                              reason: str = "") -> None:
    """Update a submission's status (submitted/quoted/declined/passed/bound/no_response)."""
    with db() as conn:
        conn.execute(
            """UPDATE market_submissions
               SET status = ?, last_contact_at = datetime('now'),
                   declination_reason = COALESCE(NULLIF(?, ''), declination_reason)
               WHERE id = ?""",
            (status, reason, submission_id),
        )


def get_submission_summary(workflow_id: str) -> dict:
    """Return a summary of submission statuses for a workflow."""
    subs = get_submissions(workflow_id)
    by_status: dict[str, list] = {}
    for s in subs:
        st = s.get("status", "submitted")
        by_status.setdefault(st, []).append(s.get("market_name", ""))
    return {
        "total": len(subs),
        "by_status": by_status,
        "quoted_count": len(by_status.get("quoted", [])),
        "pending_count": len([s for s in subs if s.get("status") == "submitted"]),
        "declined_count": len(by_status.get("declined", [])),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MARKET QUOTES (entered by humans as responses come in)
# ─────────────────────────────────────────────────────────────────────────────

def record_market_quote(
    submission_id: str,
    workflow_id: str,
    market_id: str,
    per_claim_limit: int,
    aggregate_limit: int,
    deductible: int,
    annual_premium: float,
    carrier: str = "",
    quote_number: str = "",
    effective_date: str = "",
    expiration_date: str = "",
    retroactive_date: str = "",
    prior_acts: bool = True,
    exclusions: list[str] = None,
    conditions: list[str] = None,
    valid_through: str = "",
    notes: str = "",
    entered_by: str = "human",
) -> dict:
    """Record a quote received from a market. Marks the submission as 'quoted'."""
    q_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    with db() as conn:
        conn.execute(
            """INSERT INTO market_quotes
               (id, submission_id, workflow_id, market_id, quote_number, carrier,
                per_claim_limit, aggregate_limit, deductible, annual_premium,
                effective_date, expiration_date, retroactive_date, prior_acts,
                exclusions_json, conditions_json, valid_through, notes,
                entered_by, entered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                q_id, submission_id, workflow_id, market_id,
                quote_number, carrier or _get_market_name(market_id, conn),
                per_claim_limit, aggregate_limit, deductible, annual_premium,
                effective_date, expiration_date, retroactive_date,
                1 if prior_acts else 0,
                json.dumps(exclusions or []),
                json.dumps(conditions or []),
                valid_through, notes, entered_by, now,
            ),
        )
        # Mark submission as quoted
        conn.execute(
            "UPDATE market_submissions SET status = 'quoted', last_contact_at = ? WHERE id = ?",
            (now, submission_id),
        )
    return {"id": q_id, "submission_id": submission_id, "status": "recorded"}


def _get_market_name(market_id: str, conn) -> str:
    row = conn.execute("SELECT name FROM markets WHERE id = ?", (market_id,)).fetchone()
    return row["name"] if row else ""


def get_market_quotes(workflow_id: str) -> list[dict]:
    """Return all market quotes received for a workflow."""
    with db() as conn:
        rows = conn.execute(
            """SELECT mq.*, m.name AS market_name, m.contact_email,
                      ms.submitted_at AS submission_date
               FROM market_quotes mq
               JOIN markets m ON mq.market_id = m.id
               JOIN market_submissions ms ON mq.submission_id = ms.id
               WHERE mq.workflow_id = ?
               ORDER BY mq.annual_premium""",
            (workflow_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        for field in ("exclusions_json", "conditions_json"):
            if d.get(field):
                try:
                    d[field.replace("_json", "")] = json.loads(d[field])
                except Exception:
                    d[field.replace("_json", "")] = []
        result.append(d)
    return result


def accept_market_quote(market_quote_id: str, workflow_id: str) -> dict:
    """
    Mark a market quote as accepted and create a record in the `quotes` table
    so downstream agents (BindingAgent) can find it.
    """
    with db() as conn:
        # Mark this quote accepted, decline all others
        conn.execute(
            "UPDATE market_quotes SET status = 'accepted' WHERE id = ?",
            (market_quote_id,),
        )
        conn.execute(
            """UPDATE market_quotes SET status = 'declined'
               WHERE workflow_id = ? AND id != ? AND status != 'declined'""",
            (workflow_id, market_quote_id),
        )
        # Mark associated submission as bound
        conn.execute(
            """UPDATE market_submissions SET status = 'bound'
               WHERE id = (SELECT submission_id FROM market_quotes WHERE id = ?)""",
            (market_quote_id,),
        )
        # Fetch the accepted quote
        row = conn.execute(
            """SELECT mq.*, m.name AS market_name
               FROM market_quotes mq
               JOIN markets m ON mq.market_id = m.id
               WHERE mq.id = ?""",
            (market_quote_id,),
        ).fetchone()
        if not row:
            return {"error": "Quote not found"}
        q = row_to_dict(row)

        # Write to quotes table for BindingAgent/InvoicingAgent
        q_id = str(uuid.uuid4())
        label = f"{q.get('market_name', 'Market')} — ${q.get('annual_premium', 0):,.0f}/yr"
        conn.execute(
            """INSERT OR IGNORE INTO quotes
               (id, workflow_id, option_label, per_claim_limit, aggregate_limit,
                deductible, annual_premium, base_premium, status, accepted_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', datetime('now'), ?)""",
            (
                q_id, workflow_id, label,
                q.get("per_claim_limit"), q.get("aggregate_limit"),
                q.get("deductible"), q.get("annual_premium"),
                q.get("annual_premium"),  # base_premium = final premium (market quoted)
                f"Carrier: {q.get('carrier','')} | Quote#: {q.get('quote_number','')}",
            ),
        )
    return {"quotes_id": q_id, "market_quote_id": market_quote_id, "status": "accepted"}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _days_from_now(days: int) -> str:
    from datetime import timedelta
    return (date.today() + timedelta(days=days)).isoformat()
