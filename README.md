# AgencyBill

An agentic workflow application that automates the full **Agency Bill renewal process** for **Lawyers Professional Liability (LPL)** insurance, powered by the [Anthropic Claude API](https://docs.anthropic.com/).

## What is "Agency Bill"?

In insurance, **agency bill** means the broker/agent collects the premium from the insured (the law firm) and remits it to the carrier — as opposed to *direct bill*, where the carrier bills the insured directly. This app models the complete lifecycle from an MGA's perspective:

```
Law Firm (insured) → pays → Broker/Agent → remits net → Carrier
                                ↑
                         (retains commission)
```

---

## Overview

Eight specialized Claude AI agents handle each phase of the renewal process. A master **WorkflowOrchestrator** sequences them, pausing at key human-in-the-loop (HITL) decision gates. All state is persisted in SQLite so workflows can be paused and resumed.

### Workflow Phases

```
Phase 1 ── Pre-Renewal Triage     Pull policy, firm, attorney, claims data
Phase 2 ── Renewal Application    Send questionnaire, collect insured info
Phase 3 ── Underwriting Review    Risk scoring, loss runs, eligibility decision  ← HITL
Phase 4 ── Quote Generation       Actuarial rating model → 3 coverage options
Phase 5 ── Quote Negotiation      Present to broker, record acceptance           ← HITL
Phase 6 ── Binding & Issuance     Bind coverage, issue binder + declarations     ← HITL
Phase 7 ── Invoicing & Payment    Create invoice (agency bill), record payment   ← HITL
Phase 8 ── Premium Remittance     Remit 85% to carrier, retain 15% commission   ← HITL
```

---

## Architecture

```
agencybill/
├── cli.py              # Click CLI entry point
├── orchestrator.py     # Phase sequencer with HITL gates
├── checkpoint.py       # Human-in-the-loop prompting
├── config.py           # Env vars, rating constants
├── database.py         # SQLite connection + schema init
│
├── agents/             # One Claude agent per phase
│   ├── base.py         # Agentic tool-use loop (Anthropic SDK)
│   ├── pre_renewal.py
│   ├── application.py
│   ├── underwriting.py
│   ├── quoting.py
│   ├── negotiation.py
│   ├── binding.py
│   ├── invoicing.py
│   └── remittance.py
│
├── tools/              # Tool implementations called by agents
│   ├── policy_tools.py
│   ├── firm_tools.py
│   ├── claims_tools.py
│   ├── document_tools.py
│   ├── notification_tools.py
│   ├── payment_tools.py
│   └── audit_tools.py
│
├── templates/          # Jinja2 HTML document templates
│   ├── renewal_questionnaire.html
│   ├── underwriting_memo.html
│   ├── quote_presentation.html
│   ├── binder.html
│   ├── policy_declarations.html
│   ├── invoice.html
│   └── non_renewal_notice.html
│
└── display/
    └── console.py      # Rich terminal UI helpers

data/
├── fixtures/           # Sample seed data (5 LPL policies)
└── agencybill.db       # SQLite database (created at runtime)

output/                 # Generated HTML documents
```

### How the Agentic Loop Works

Each agent extends `BaseAgent`, which implements a multi-turn tool-use loop using the Anthropic SDK:

```
1. Send system prompt + workflow context to claude-sonnet-4-6
2. Receive response (may contain tool_use blocks)
3. Execute tool calls → write results to audit_events table
4. Feed tool results back as tool_result blocks
5. Loop until stop_reason == "end_turn" (agent is done)
6. Orchestrator advances to next phase
```

All tool calls are logged to an `audit_events` table, providing a complete trace of every agent decision.

---

## Installation

**Requirements:** Python 3.11+, an Anthropic API key.

```bash
git clone <repo-url>
cd AgencyBill

pip install -e .

cp .env.example .env
# Edit .env and set: ANTHROPIC_API_KEY=sk-ant-...
```

---

## Quick Start

```bash
# Load sample data (5 law firms, 5 LPL policies, 4 claims)
agencybill seed

# See which policies are coming up for renewal
agencybill list-expiring --days 365

# Start the agentic renewal workflow for a policy
agencybill start --policy-id pol-001

# Or run fully automated (skip all HITL checkpoints)
agencybill start --policy-id pol-001 --auto
```

The `start` command runs all 8 phases sequentially. At HITL checkpoints, you'll see a prompt like:

```
╔══════════════════════════════════════════════════════╗
║  HUMAN CHECKPOINT — UNDERWRITING                     ║
╠══════════════════════════════════════════════════════╣
║  Underwriting has completed its review. What is      ║
║  the underwriting decision?                          ║
╠══════════════════════════════════════════════════════╣
║  [1] Approve — proceed to quoting                    ║
║  [2] Refer — proceed with notation                   ║
║  [3] Decline renewal                                 ║
╚══════════════════════════════════════════════════════╝
Your choice: 1
```

---

## CLI Reference

| Command | Description |
|---|---|
| `agencybill seed` | Load sample firms, policies, claims into DB |
| `agencybill list-expiring [--days N]` | List active policies expiring within N days (default: 90) |
| `agencybill policies` | List all policies in the database |
| `agencybill start --policy-id ID [--auto]` | Start a new renewal workflow |
| `agencybill resume --workflow-id ID [--auto]` | Resume a paused workflow |
| `agencybill status [--workflow-id ID]` | Show workflow status (all or one) |
| `agencybill audit --workflow-id ID` | Print full audit trail with every agent action |

---

## Sample Data

The seed fixtures include 5 law firms across multiple states and risk profiles:

| Policy | Firm | State | Type | Premium | Expires |
|---|---|---|---|---|---|
| LPL-2024-00101 | Harrison & Cole LLP | NY | Mid (4 atty) | $48,500 | 2026-07-01 |
| LPL-2024-00202 | Whitfield & Associates | CA | Small (2 atty) | $18,200 | 2026-06-15 |
| LPL-2024-00303 | Ortega Legal Group | TX | Solo | $9,800 | 2026-06-01 |
| LPL-2024-00404 | Mercer Blackwell LLP | IL | Large (4+ atty) | $142,000 | 2026-06-15 |
| LPL-2024-00505 | Caldwell Family Law | KY | Small (2 atty) | $14,600 | 2026-07-01 |

---

## Premium Rating Model

The `QuotingAgent` uses a simplified actuarial rating model for LPL:

```
Premium = Base Rate per Attorney
        × Attorney Count
        × Practice Area Modifier   (litigation ↑, transactional ↓)
        × Experience Modifier      (derived from 5-year loss ratio)
        × Revenue Modifier
        × (1 − Deductible Credit)
```

**State tiers** (base rate per attorney for 1M/1M limits):

| Tier | States | Rate |
|---|---|---|
| Tier 1 (highest) | CA, NY, FL, TX, IL | $3,200 |
| Tier 2 | PA, OH, GA, NJ, WA | $2,600 |
| Tier 3 (all others) | — | $2,100 |

**Practice area modifiers:**

| Area | Modifier |
|---|---|
| Securities | 1.50× |
| Personal Injury | 1.40× |
| Litigation | 1.35× |
| Employment | 1.25× |
| Family Law | 1.20× |
| Criminal Defense | 1.15× |
| Real Estate | 1.10× |
| IP | 1.05× |
| General Practice | 1.00× |
| Corporate | 0.95× |
| Estate Planning | 0.85× |

**Deductible credits:** $2,500 (0%) → $5,000 (5%) → $10,000 (10%) → $25,000 (18%) → $50,000 (25%)

---

## Database Schema

The SQLite database (`data/agencybill.db`) has 14 tables:

| Table | Purpose |
|---|---|
| `law_firms` | Insured law firm profiles |
| `attorneys` | Attorney roster per firm |
| `brokers` | Agent/broker accounts |
| `policies` | LPL policy records |
| `claims` | Claims history and loss runs |
| `renewal_workflows` | One workflow per renewal cycle |
| `workflow_events` | Phase-level event log |
| `applications` | Renewal questionnaire data |
| `quotes` | Generated quote options |
| `invoices` | Premium invoices to broker (agency bill) |
| `payments` | Payments received from broker |
| `remittances` | Premium remitted to carrier |
| `human_checkpoints` | HITL decisions with full audit |
| `audit_events` | Complete agent action log |

---

## Configuration

All settings are controlled via environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `AGENCYBILL_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `AGENCYBILL_DB_PATH` | `data/agencybill.db` | SQLite database path |
| `AGENCYBILL_OUTPUT_DIR` | `output` | Directory for generated documents |
| `AGENCYBILL_MAX_AGENT_TURNS` | `20` | Max tool-use turns per agent phase |

---

## Generated Documents

Each phase produces HTML documents saved to `output/`:

| Phase | Document |
|---|---|
| Application | `renewal_questionnaire_*.html` |
| Underwriting | `underwriting_memo_*.html` |
| Quoting | `quote_presentation_*.html` |
| Binding | `binder_*.html`, `policy_declarations_*.html` |
| Invoicing | `invoice_*.html` |
| Non-renewal | `non_renewal_notice_*.html` |

---

## Non-Renewal & Declination

If the underwriting agent scores the risk too high (score ≥ 8.5 or > 8 claims in 5 years), or the human selects "Decline" at the HITL checkpoint, the orchestrator routes to a non-renewal path:

1. Workflow status set to `non_renewal`
2. `non_renewal_notice.html` generated
3. Policy flagged for non-renewal in the database

---

## Tech Stack

| Component | Library |
|---|---|
| AI agents | [anthropic](https://github.com/anthropics/anthropic-sdk-python) (`claude-sonnet-4-6`) |
| CLI | [click](https://click.palletsprojects.com/) |
| Terminal UI | [rich](https://github.com/Textualize/rich) |
| Document templates | [jinja2](https://jinja.palletsprojects.com/) |
| Database | SQLite (built-in `sqlite3`) |
| Config | [python-dotenv](https://github.com/theskumar/python-dotenv) |
