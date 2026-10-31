"""Application configuration loaded from environment variables."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
# When set, every agent LLM call goes through OpenRouter instead of Anthropic directly.
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api").rstrip("/")
DB_PATH: Path = BASE_DIR / os.getenv("AGENCYBILL_DB_PATH", "data/agencybill.db")
OUTPUT_DIR: Path = BASE_DIR / os.getenv("AGENCYBILL_OUTPUT_DIR", "output")
MODEL: str = os.getenv("AGENCYBILL_MODEL", "claude-sonnet-4-6")
MAX_AGENT_TURNS: int = int(os.getenv("AGENCYBILL_MAX_AGENT_TURNS", "20"))

# Web basic auth — set AGENCYBILL_WEB_PASSWORD to enable; empty disables with a warning
WEB_USER: str = os.getenv("AGENCYBILL_WEB_USER", "agencybill")
WEB_PASSWORD: str = os.getenv("AGENCYBILL_WEB_PASSWORD", "")

FIXTURES_DIR: Path = BASE_DIR / "data" / "fixtures"
TEMPLATES_DIR: Path = BASE_DIR / "agencybill" / "templates"

# Ensure output dir exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Default agency-bill commission retained by the broker
DEFAULT_COMMISSION_PCT: float = float(os.getenv("AGENCYBILL_COMMISSION_PCT", "0.15"))

# Workflow phases in order (single source of truth)
PHASES = [
    "PRE_RENEWAL",
    "APPLICATION",
    "UNDERWRITING",
    "MARKET_SUBMISSION",
    "QUOTE_COLLECTION",
    "BINDING",
    "INVOICING",
    "REMITTANCE",
    "COMPLETE",
]

# Phases that have an agent (excludes terminal COMPLETE)
AGENT_PHASES = [p for p in PHASES if p != "COMPLETE"]

PHASE_LABELS = {
    "PRE_RENEWAL":        "Phase 1 — Pre-Renewal Triage",
    "APPLICATION":        "Phase 2 — Renewal Application",
    "UNDERWRITING":       "Phase 3 — Underwriting Review",
    "MARKET_SUBMISSION":  "Phase 4 — Market Submission",
    "QUOTE_COLLECTION":   "Phase 5 — Quote Collection",
    "BINDING":            "Phase 6 — Binding & Issuance",
    "INVOICING":          "Phase 7 — Invoicing & Payment",
    "REMITTANCE":         "Phase 8 — Premium Remittance",
    "COMPLETE":           "Complete",
    "NON_RENEWAL":        "Non-Renewal",
    "DECLINED":           "Declined",
}

PHASE_COLORS = {
    "PRE_RENEWAL":       "cyan",
    "APPLICATION":       "blue",
    "UNDERWRITING":      "yellow",
    "MARKET_SUBMISSION": "magenta",
    "QUOTE_COLLECTION":  "bright_yellow",
    "BINDING":           "green",
    "INVOICING":         "bright_cyan",
    "REMITTANCE":        "bright_green",
    "COMPLETE":          "bold green",
    "NON_RENEWAL":       "red",
    "DECLINED":          "bold red",
}

# Deterministic next-phase map (orchestrator advances after successful agent runs)
NEXT_PHASE = {
    "PRE_RENEWAL":       "APPLICATION",
    "APPLICATION":       "UNDERWRITING",
    "UNDERWRITING":      "MARKET_SUBMISSION",
    "MARKET_SUBMISSION": "QUOTE_COLLECTION",
    "QUOTE_COLLECTION":  "BINDING",
    "BINDING":           "INVOICING",
    "INVOICING":         "REMITTANCE",
    "REMITTANCE":        "COMPLETE",
}


# Anthropic ids end in -<major>-<minor> (claude-sonnet-4-6). OpenRouter uses dots
# (anthropic/claude-sonnet-4.6). Dated ids (…-20251001) are left unchanged.
_ANTHROPIC_VERSION_SUFFIX = re.compile(r"-(\d+)-(\d{1,2})$")


@dataclass(frozen=True)
class LLMSettings:
    """Resolved provider for every agent LLM call."""

    provider: str  # "openrouter", "anthropic", or "" when unconfigured
    api_key: str
    base_url: str | None
    model: str

    @property
    def configured(self) -> bool:
        return self.provider in ("openrouter", "anthropic") and bool(self.api_key)


def openrouter_model_id(model: str) -> str:
    """Map an Anthropic model id to an OpenRouter slug.

    A value that already contains "/" is used as-is so callers can pick any
    OpenRouter model (``openai/gpt-4.1``, ``anthropic/claude-sonnet-4.6``).
    """
    model = (model or "").strip()
    if not model or "/" in model:
        return model
    model = _ANTHROPIC_VERSION_SUFFIX.sub(r"-\1.\2", model)
    return f"anthropic/{model}"


def resolve_llm_settings() -> LLMSettings:
    """Choose OpenRouter when its key is set; otherwise the Anthropic API.

    ``OPENROUTER_API_KEY`` wins when both keys are present so one variable
    routes every agent call.
    """
    openrouter_key = OPENROUTER_API_KEY.strip()
    anthropic_key = ANTHROPIC_API_KEY.strip()
    model = MODEL.strip() or "claude-sonnet-4-6"
    if openrouter_key:
        base = (OPENROUTER_BASE_URL or "https://openrouter.ai/api").rstrip("/")
        return LLMSettings(
            provider="openrouter",
            api_key=openrouter_key,
            base_url=base,
            model=openrouter_model_id(model),
        )
    if anthropic_key:
        return LLMSettings(
            provider="anthropic",
            api_key=anthropic_key,
            base_url=None,
            model=model,
        )
    return LLMSettings(provider="", api_key="", base_url=None, model=model)


def llm_footer_label() -> str:
    """Short provider/model line for the web UI footer."""
    settings = resolve_llm_settings()
    if settings.provider == "openrouter":
        return f"OpenRouter · {settings.model}"
    if settings.provider == "anthropic":
        return f"Claude · {settings.model}"
    return "LLM not configured"


def phase_pct(phase: str) -> int:
    """Return 0–100 progress for a phase name."""
    try:
        return round((PHASES.index(phase) + 1) / len(PHASES) * 100)
    except ValueError:
        return 0

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
