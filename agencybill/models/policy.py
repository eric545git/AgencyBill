"""Policy and workflow dataclasses."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Policy:
    id: str
    firm_id: str
    policy_number: str
    carrier: str
    effective_date: str
    expiration_date: str
    limit_per_claim: int
    aggregate_limit: int
    deductible: int
    annual_premium: float
    status: str = "active"
    attorney_count: int = 0
    broker_id: Optional[str] = None
    created_at: str = ""


@dataclass
class RenewalWorkflow:
    id: str
    policy_id: str
    phase: str = "PRE_RENEWAL"
    status: str = "active"
    renewal_year: Optional[int] = None
    started_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    assigned_underwriter: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Quote:
    id: str
    workflow_id: str
    option_label: str
    per_claim_limit: int
    aggregate_limit: int
    deductible: int
    annual_premium: float
    base_premium: float = 0.0
    experience_mod: float = 1.0
    practice_area_mod: float = 1.0
    revenue_mod: float = 1.0
    deductible_credit: float = 0.0
    status: str = "draft"
    generated_at: str = ""
    presented_at: Optional[str] = None
    accepted_at: Optional[str] = None
    expires_at: Optional[str] = None
    notes: Optional[str] = None
