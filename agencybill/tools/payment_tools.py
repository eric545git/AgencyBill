"""Tools for invoice creation, payment recording, and premium remittance."""

import uuid
from datetime import datetime, date, timedelta

from agencybill.database import db, row_to_dict


def create_invoice(workflow_id: str, amount_due: float,
                    days_until_due: int = 30) -> dict:
    """Create an invoice for the renewal premium."""
    inv_id = str(uuid.uuid4())
    invoice_number = f"INV-{datetime.now().strftime('%Y%m%d')}-{inv_id[:6].upper()}"
    due_date = (date.today() + timedelta(days=days_until_due)).isoformat()
    with db() as conn:
        conn.execute(
            """INSERT INTO invoices (id, workflow_id, invoice_number, amount_due, due_date)
               VALUES (?, ?, ?, ?, ?)""",
            (inv_id, workflow_id, invoice_number, amount_due, due_date),
        )
    return {
        "id": inv_id,
        "workflow_id": workflow_id,
        "invoice_number": invoice_number,
        "amount_due": amount_due,
        "due_date": due_date,
        "status": "unpaid",
    }


def get_invoice(invoice_id: str) -> dict:
    """Return invoice by ID."""
    with db() as conn:
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return row_to_dict(row)


def get_invoices_for_workflow(workflow_id: str) -> list[dict]:
    """Return all invoices for a workflow."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE workflow_id = ? ORDER BY issued_at DESC",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def record_payment(invoice_id: str, amount: float,
                    method: str = "check", reference: str = "") -> dict:
    """Record a premium payment against an invoice."""
    pay_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO payments (id, invoice_id, amount, method, reference)
               VALUES (?, ?, ?, ?, ?)""",
            (pay_id, invoice_id, amount, method, reference),
        )
        # Check if fully paid
        inv = conn.execute(
            "SELECT amount_due FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        total_paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()[0]
        if inv and total_paid >= inv["amount_due"]:
            conn.execute(
                "UPDATE invoices SET status = 'paid', paid_at = datetime('now') WHERE id = ?",
                (invoice_id,),
            )
        elif total_paid > 0:
            conn.execute(
                "UPDATE invoices SET status = 'partial' WHERE id = ?", (invoice_id,)
            )
    return {"id": pay_id, "invoice_id": invoice_id, "amount": amount,
            "method": method, "reference": reference}


def create_remittance(workflow_id: str, carrier: str, gross_premium: float,
                       commission_pct: float = 0.15) -> dict:
    """Create a remittance record (net of commission)."""
    rem_id = str(uuid.uuid4())
    net_commission = round(gross_premium * commission_pct, 2)
    carrier_amount = round(gross_premium - net_commission, 2)
    due_date = (date.today() + timedelta(days=30)).isoformat()
    ref = f"REM-{datetime.now().strftime('%Y%m')}-{rem_id[:6].upper()}"
    with db() as conn:
        conn.execute(
            """INSERT INTO remittances
               (id, workflow_id, carrier, amount, net_commission, due_date, reference_number)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rem_id, workflow_id, carrier, carrier_amount, net_commission, due_date, ref),
        )
    return {
        "id": rem_id,
        "workflow_id": workflow_id,
        "carrier": carrier,
        "gross_premium": gross_premium,
        "net_commission": net_commission,
        "carrier_amount": carrier_amount,
        "due_date": due_date,
        "reference_number": ref,
        "status": "pending",
    }


def mark_remittance_sent(remittance_id: str) -> None:
    """Mark a remittance as sent to the carrier."""
    with db() as conn:
        conn.execute(
            "UPDATE remittances SET status = 'remitted', remitted_at = datetime('now') WHERE id = ?",
            (remittance_id,),
        )


def get_remittances(workflow_id: str) -> list[dict]:
    """Return all remittances for a workflow."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM remittances WHERE workflow_id = ? ORDER BY due_date",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]
