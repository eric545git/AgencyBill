"""Claim dataclass."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Claim:
    id: str
    policy_id: str
    firm_id: str
    claim_date: str
    claim_number: Optional[str] = None
    closed_date: Optional[str] = None
    claim_type: Optional[str] = None
    practice_area: Optional[str] = None
    amount_paid: float = 0.0
    amount_reserved: float = 0.0
    status: str = "open"
    description: Optional[str] = None
    created_at: str = ""
