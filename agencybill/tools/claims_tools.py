"""Tools for claims history and loss run generation."""

import uuid
import json
from datetime import datetime, date

from agencybill.database import db, row_to_dict


def get_claims_for_policy(policy_id: str) -> list[dict]:
    """Return all claims for a given policy."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM claims WHERE policy_id = ? ORDER BY claim_date DESC",
            (policy_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_claims_for_firm(firm_id: str, years: int = 5) -> list[dict]:
    """Return claims across all policies for a firm within the last N years."""
    cutoff = f"{date.today().year - years}-01-01"
    with db() as conn:
        rows = conn.execute(
            """SELECT c.*, p.policy_number, p.carrier
               FROM claims c
               JOIN policies p ON c.policy_id = p.id
               WHERE c.firm_id = ? AND c.claim_date >= ?
               ORDER BY c.claim_date DESC""",
            (firm_id, cutoff),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def generate_loss_run(firm_id: str, years: int = 5) -> dict:
    """Generate a loss run summary for underwriting review."""
    claims = get_claims_for_firm(firm_id, years)
    total_paid = sum(c.get("amount_paid", 0) for c in claims)
    total_reserved = sum(c.get("amount_reserved", 0) for c in claims)
    open_claims = [c for c in claims if c.get("status") == "open"]
    closed_claims = [c for c in claims if c.get("status") == "closed"]

    # Group by year
    by_year: dict[str, dict] = {}
    for c in claims:
        yr = c["claim_date"][:4]
        if yr not in by_year:
            by_year[yr] = {"count": 0, "paid": 0, "reserved": 0}
        by_year[yr]["count"] += 1
        by_year[yr]["paid"] += c.get("amount_paid", 0)
        by_year[yr]["reserved"] += c.get("amount_reserved", 0)

    return {
        "firm_id": firm_id,
        "years_reviewed": years,
        "total_claims": len(claims),
        "open_claims": len(open_claims),
        "closed_claims": len(closed_claims),
        "total_paid": total_paid,
        "total_reserved": total_reserved,
        "total_incurred": total_paid + total_reserved,
        "by_year": by_year,
        "claims": claims,
        "generated_at": datetime.now().isoformat(),
    }


def compute_experience_modifier(loss_run: dict, current_premium: float) -> float:
    """Compute a simple experience modifier based on loss ratio."""
    if current_premium <= 0:
        return 1.0
    incurred = loss_run.get("total_incurred", 0)
    loss_ratio = incurred / (current_premium * loss_run.get("years_reviewed", 5))
    if loss_ratio < 0.20:
        return 0.90   # credit
    elif loss_ratio < 0.40:
        return 1.00   # flat
    elif loss_ratio < 0.70:
        return 1.15   # debit
    elif loss_ratio < 1.00:
        return 1.30   # debit
    else:
        return 1.50   # significant debit


def add_claim(policy_id: str, firm_id: str, claim_data: dict) -> str:
    """Insert a new claim record. Returns the new claim ID."""
    claim_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO claims
               (id, policy_id, firm_id, claim_number, claim_date, claim_type,
                practice_area, amount_paid, amount_reserved, status, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                claim_id, policy_id, firm_id,
                claim_data.get("claim_number"),
                claim_data.get("claim_date", date.today().isoformat()),
                claim_data.get("claim_type"),
                claim_data.get("practice_area"),
                claim_data.get("amount_paid", 0),
                claim_data.get("amount_reserved", 0),
                claim_data.get("status", "open"),
                claim_data.get("description"),
            ),
        )
    return claim_id


def assess_risk(loss_run: dict, practice_areas: list[str], attorney_count: int,
                firm_type: str) -> dict:
    """Produce a basic underwriting risk assessment."""
    from agencybill.config import PRACTICE_AREA_MODIFIERS

    # Risk score 1-10 (10 = highest risk)
    score = 5.0

    # Claim frequency factor
    total_claims = loss_run.get("total_claims", 0)
    if total_claims == 0:
        score -= 1.5
    elif total_claims <= 2:
        score += 0.5
    elif total_claims <= 5:
        score += 1.5
    else:
        score += 3.0

    # Practice area risk
    area_mods = [PRACTICE_AREA_MODIFIERS.get(a, 1.0) for a in practice_areas]
    avg_mod = sum(area_mods) / len(area_mods) if area_mods else 1.0
    if avg_mod > 1.30:
        score += 1.5
    elif avg_mod > 1.10:
        score += 0.5
    elif avg_mod < 0.90:
        score -= 0.5

    # Firm size
    if firm_type == "large":
        score += 0.5
    elif firm_type == "solo":
        score += 0.3

    score = max(1.0, min(10.0, score))

    # Eligibility
    if score >= 8.5 or total_claims > 8:
        eligibility = "declined"
        reason = "Risk score too high or excessive claim frequency"
    elif score >= 7.0 or total_claims > 5:
        eligibility = "refer"
        reason = "Requires senior underwriter review"
    else:
        eligibility = "eligible"
        reason = "Standard renewal eligible"

    return {
        "risk_score": round(score, 1),
        "eligibility": eligibility,
        "reason": reason,
        "avg_practice_area_modifier": round(avg_mod, 3),
        "total_claims_5yr": total_claims,
        "total_incurred_5yr": loss_run.get("total_incurred", 0),
    }
