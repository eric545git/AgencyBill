"""Application configuration loaded from environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DB_PATH: Path = BASE_DIR / os.getenv("AGENCYBILL_DB_PATH", "data/agencybill.db")
OUTPUT_DIR: Path = BASE_DIR / os.getenv("AGENCYBILL_OUTPUT_DIR", "output")
MODEL: str = os.getenv("AGENCYBILL_MODEL", "claude-sonnet-4-6")
MAX_AGENT_TURNS: int = int(os.getenv("AGENCYBILL_MAX_AGENT_TURNS", "20"))

FIXTURES_DIR: Path = BASE_DIR / "data" / "fixtures"
TEMPLATES_DIR: Path = BASE_DIR / "agencybill" / "templates"

# Ensure output dir exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Workflow phases in order
PHASES = [
    "PRE_RENEWAL",
    "APPLICATION",
    "UNDERWRITING",
    "QUOTING",
    "NEGOTIATION",
    "BINDING",
    "INVOICING",
    "REMITTANCE",
    "COMPLETE",
]

PHASE_LABELS = {
    "PRE_RENEWAL":   "Phase 1 — Pre-Renewal Triage",
    "APPLICATION":   "Phase 2 — Renewal Application",
    "UNDERWRITING":  "Phase 3 — Underwriting Review",
    "QUOTING":       "Phase 4 — Quote Generation",
    "NEGOTIATION":   "Phase 5 — Quote Negotiation",
    "BINDING":       "Phase 6 — Binding & Issuance",
    "INVOICING":     "Phase 7 — Invoicing & Payment",
    "REMITTANCE":    "Phase 8 — Premium Remittance",
    "COMPLETE":      "Complete",
    "NON_RENEWAL":   "Non-Renewal",
    "DECLINED":      "Declined",
}

# LPL base rates per attorney by state tier (simplified)
STATE_TIER = {
    "tier1": ["CA", "NY", "FL", "TX", "IL"],  # highest risk
    "tier2": ["PA", "OH", "GA", "NJ", "WA"],
    "tier3": [],  # all others
}

BASE_RATE_PER_ATTORNEY = {
    "tier1": {"1M/1M": 3200, "1M/2M": 3800, "2M/4M": 5100},
    "tier2": {"1M/1M": 2600, "1M/2M": 3100, "2M/4M": 4200},
    "tier3": {"1M/1M": 2100, "1M/2M": 2500, "2M/4M": 3400},
}

PRACTICE_AREA_MODIFIERS = {
    "litigation":          1.35,
    "personal_injury":     1.40,
    "securities":          1.50,
    "real_estate":         1.10,
    "corporate":           0.95,
    "estate_planning":     0.85,
    "family_law":          1.20,
    "criminal_defense":    1.15,
    "intellectual_property": 1.05,
    "employment":          1.25,
    "general_practice":    1.00,
}

DEDUCTIBLE_CREDITS = {
    2500:   0.00,
    5000:   0.05,
    10000:  0.10,
    25000:  0.18,
    50000:  0.25,
}
