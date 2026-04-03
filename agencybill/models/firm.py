"""Law firm, attorney, broker dataclasses."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class LawFirm:
    id: str
    name: str
    state: str
    firm_type: str = "small"
    tax_id: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    year_founded: Optional[int] = None
    created_at: str = ""


@dataclass
class Attorney:
    id: str
    firm_id: str
    name: str
    bar_number: Optional[str] = None
    state_licensed: Optional[str] = None
    practice_areas: Optional[str] = None   # JSON string
    years_admitted: Optional[int] = None
    active: int = 1


@dataclass
class Broker:
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    license_number: Optional[str] = None
    state: Optional[str] = None
    agency_name: Optional[str] = None
