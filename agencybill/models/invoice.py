"""Invoice, Payment, Remittance dataclasses."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Invoice:
    id: str
    workflow_id: str
    invoice_number: str
    amount_due: float
    status: str = "unpaid"
    issued_at: str = ""
    due_date: Optional[str] = None
    paid_at: Optional[str] = None


@dataclass
class Payment:
    id: str
    invoice_id: str
    amount: float
    received_at: str = ""
    method: str = "check"
    reference: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Remittance:
    id: str
    workflow_id: str
    carrier: str
    amount: float
    net_commission: float = 0.0
    status: str = "pending"
    remitted_at: Optional[str] = None
    due_date: Optional[str] = None
    reference_number: Optional[str] = None
