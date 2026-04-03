"""Phase 4 — Quote Generation Agent."""

import uuid
import json
from datetime import datetime, date, timedelta

from agencybill.agents.base import BaseAgent
from agencybill.tools.policy_tools import read_policy, update_workflow_phase, log_workflow_event
from agencybill.tools.firm_tools import read_firm, get_firm_practice_areas
from agencybill.tools.claims_tools import generate_loss_run, compute_experience_modifier
from agencybill.tools.document_tools import save_document
from agencybill.config import BASE_RATE_PER_ATTORNEY, STATE_TIER, PRACTICE_AREA_MODIFIERS, DEDUCTIBLE_CREDITS
from agencybill.database import db, row_to_dict


def _get_application(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
    return row_to_dict(row)


def _get_state_tier(state: str) -> str:
    for tier, states in STATE_TIER.items():
        if state.upper() in states:
            return tier
    return "tier3"


def _calculate_premium(
    state: str,
    attorney_count: int,
    practice_areas: list[str],
    loss_run: dict,
    current_premium: float,
    limit_tier: str = "1M/1M",
    deductible: int = 5000,
    gross_revenue: float = 0,
) -> dict:
    tier = _get_state_tier(state)
    base_rate = BASE_RATE_PER_ATTORNEY[tier].get(limit_tier, BASE_RATE_PER_ATTORNEY[tier]["1M/1M"])
    base_premium = base_rate * max(attorney_count, 1)

    # Practice area modifier (weighted average)
    mods = [PRACTICE_AREA_MODIFIERS.get(a, 1.0) for a in practice_areas]
    pa_mod = round(sum(mods) / len(mods), 3) if mods else 1.0

    # Experience modifier
    incurred = loss_run.get("total_incurred", 0)
    years = loss_run.get("years_reviewed", 5)
    loss_ratio = incurred / (current_premium * years) if current_premium > 0 else 0
    if loss_ratio < 0.20:
        exp_mod = 0.90
    elif loss_ratio < 0.40:
        exp_mod = 1.00
    elif loss_ratio < 0.70:
        exp_mod = 1.15
    elif loss_ratio < 1.00:
        exp_mod = 1.30
    else:
        exp_mod = 1.50

    # Revenue modifier (small credit for lower-revenue firms)
    if gross_revenue > 0:
        if gross_revenue < 500000:
            rev_mod = 0.95
        elif gross_revenue < 2000000:
            rev_mod = 1.00
        else:
            rev_mod = 1.05
    else:
        rev_mod = 1.00

    # Deductible credit
    ded_credit = DEDUCTIBLE_CREDITS.get(deductible, 0.0)

    annual_premium = base_premium * pa_mod * exp_mod * rev_mod * (1 - ded_credit)
    annual_premium = round(annual_premium, 2)

    return {
        "base_premium": base_premium,
        "pa_mod": pa_mod,
        "exp_mod": exp_mod,
        "rev_mod": rev_mod,
        "ded_credit": ded_credit,
        "annual_premium": annual_premium,
        "limit_tier": limit_tier,
        "deductible": deductible,
    }


def _create_quote(
    workflow_id: str,
    option_label: str,
    per_claim_limit: int,
    aggregate_limit: int,
    deductible: int,
    annual_premium: float,
    base_premium: float,
    experience_mod: float,
    practice_area_mod: float,
    revenue_mod: float,
    deductible_credit: float,
    notes: str = "",
) -> dict:
    q_id = str(uuid.uuid4())
    expires_at = (date.today() + timedelta(days=30)).isoformat()
    with db() as conn:
        conn.execute(
            """INSERT INTO quotes
               (id, workflow_id, option_label, per_claim_limit, aggregate_limit,
                deductible, annual_premium, base_premium, experience_mod,
                practice_area_mod, revenue_mod, deductible_credit, expires_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (q_id, workflow_id, option_label, per_claim_limit, aggregate_limit,
             deductible, annual_premium, base_premium, experience_mod,
             practice_area_mod, revenue_mod, deductible_credit, expires_at, notes),
        )
    return {
        "id": q_id, "workflow_id": workflow_id, "option_label": option_label,
        "per_claim_limit": per_claim_limit, "aggregate_limit": aggregate_limit,
        "deductible": deductible, "annual_premium": annual_premium,
        "expires_at": expires_at, "status": "draft",
    }


def _get_quotes(workflow_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? ORDER BY annual_premium",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


class QuotingAgent(BaseAgent):
    """Phase 4: Generate multiple quote options using actuarial rating model."""

    name = "QuotingAgent"
    system_prompt = """You are an LPL insurance rater/quoting specialist.

Your tasks for Phase 4 Quote Generation:
1. Read the policy (read_policy) and firm (read_firm) details
2. Get the application data (get_application)
3. Get the firm's practice areas (get_firm_practice_areas)
4. Generate the loss run (generate_loss_run)
5. Calculate premiums for THREE quote options using calculate_premium:
   - Option A: 1M/1M limits, $5,000 deductible (current limits or close)
   - Option B: 1M/2M limits, $10,000 deductible
   - Option C: 2M/4M limits, $25,000 deductible
6. Create each quote record (create_quote) for all three options
7. Generate the quote presentation document (save_document with 'quote_presentation.html')
   Pass context: {firm: {...}, policy: {...}, quotes: [...]}
8. Log a workflow event
9. Advance to NEGOTIATION phase

Generate competitive, actuarially sound quotes."""

    def _register_tools(self) -> None:
        self._add_tool("read_policy", "Read policy details",
            {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
            read_policy)
        self._add_tool("read_firm", "Read firm details",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            read_firm)
        self._add_tool("get_firm_practice_areas", "Get firm practice areas",
            {"type": "object", "properties": {"firm_id": {"type": "string"}}, "required": ["firm_id"]},
            get_firm_practice_areas)
        self._add_tool("get_application", "Get renewal application",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_application)
        self._add_tool("generate_loss_run", "Generate loss run",
            {"type": "object", "properties": {
                "firm_id": {"type": "string"}, "years": {"type": "integer"},
            }, "required": ["firm_id"]},
            generate_loss_run)
        self._add_tool("calculate_premium", "Calculate premium using the LPL rating model",
            {"type": "object", "properties": {
                "state": {"type": "string"},
                "attorney_count": {"type": "integer"},
                "practice_areas": {"type": "array", "items": {"type": "string"}},
                "loss_run": {"type": "object"},
                "current_premium": {"type": "number"},
                "limit_tier": {"type": "string", "enum": ["1M/1M", "1M/2M", "2M/4M"]},
                "deductible": {"type": "integer"},
                "gross_revenue": {"type": "number"},
            }, "required": ["state", "attorney_count", "practice_areas", "loss_run", "current_premium"]},
            _calculate_premium)
        self._add_tool("create_quote", "Create a quote record",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"},
                "option_label": {"type": "string"},
                "per_claim_limit": {"type": "integer"},
                "aggregate_limit": {"type": "integer"},
                "deductible": {"type": "integer"},
                "annual_premium": {"type": "number"},
                "base_premium": {"type": "number"},
                "experience_mod": {"type": "number"},
                "practice_area_mod": {"type": "number"},
                "revenue_mod": {"type": "number"},
                "deductible_credit": {"type": "number"},
                "notes": {"type": "string"},
            }, "required": ["workflow_id", "option_label", "per_claim_limit", "aggregate_limit",
                            "deductible", "annual_premium", "base_premium", "experience_mod",
                            "practice_area_mod", "revenue_mod", "deductible_credit"]},
            _create_quote)
        self._add_tool("get_quotes", "Get all quotes for a workflow",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            _get_quotes)
        self._add_tool("save_document", "Render and save a document template",
            {"type": "object", "properties": {
                "template_name": {"type": "string"},
                "context": {"type": "object"},
                "filename_prefix": {"type": "string"},
            }, "required": ["template_name", "context"]},
            save_document)
        self._add_tool("log_workflow_event", "Log a workflow event",
            {"type": "object", "properties": {
                "workflow_id": {"type": "string"}, "phase": {"type": "string"},
                "event_type": {"type": "string"}, "actor": {"type": "string"},
                "data": {"type": "object"},
            }, "required": ["workflow_id", "phase", "event_type", "actor", "data"]},
            log_workflow_event)
        self._add_tool("advance_to_negotiation",
            "Advance workflow to NEGOTIATION phase",
            {"type": "object", "properties": {"workflow_id": {"type": "string"}}, "required": ["workflow_id"]},
            lambda workflow_id: update_workflow_phase(workflow_id, "NEGOTIATION"))

    def run_phase(self, policy_id: str, workflow_id: str) -> str:
        msg = (
            f"Begin Phase 4 Quote Generation for policy_id={policy_id}, "
            f"workflow_id={workflow_id}. "
            "Generate three competitive quote options, create quote records, "
            "produce the quote presentation document, and advance to NEGOTIATION."
        )
        return self.run(msg)
