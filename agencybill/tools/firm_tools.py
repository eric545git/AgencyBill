"""Tools for reading and writing law firm data."""

from agencybill.database import db, row_to_dict
import json


def read_firm(firm_id: str) -> dict:
    """Return law firm details."""
    with db() as conn:
        row = conn.execute("SELECT * FROM law_firms WHERE id = ?", (firm_id,)).fetchone()
    return row_to_dict(row)


def read_attorneys(firm_id: str) -> list[dict]:
    """Return all active attorneys for a firm."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM attorneys WHERE firm_id = ? AND active = 1 ORDER BY name",
            (firm_id,),
        ).fetchall()
    result = []
    for row in rows:
        d = row_to_dict(row)
        if d.get("practice_areas"):
            try:
                d["practice_areas"] = json.loads(d["practice_areas"])
            except Exception:
                pass
        result.append(d)
    return result


def get_firm_practice_areas(firm_id: str) -> list[str]:
    """Return a deduplicated list of practice areas across all firm attorneys."""
    attorneys = read_attorneys(firm_id)
    areas: set[str] = set()
    for atty in attorneys:
        pa = atty.get("practice_areas", [])
        if isinstance(pa, list):
            areas.update(pa)
        elif isinstance(pa, str):
            areas.add(pa)
    return sorted(areas)


def update_firm(firm_id: str, updates: dict) -> None:
    """Update specific fields on a law_firms record."""
    allowed = {"name", "email", "phone", "address_line1", "city", "state", "zip"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [firm_id]
    with db() as conn:
        conn.execute(f"UPDATE law_firms SET {set_clause} WHERE id = ?", values)


def read_broker(broker_id: str) -> dict:
    """Return broker/agent details."""
    with db() as conn:
        row = conn.execute("SELECT * FROM brokers WHERE id = ?", (broker_id,)).fetchone()
    return row_to_dict(row)
