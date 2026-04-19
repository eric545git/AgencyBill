# AgencyBill

An agentic workflow application that automates the full **Agency Bill renewal process** for **Lawyers Professional Liability (LPL)** insurance, powered by the [Anthropic Claude API](https://docs.anthropic.com/).

## What is "Agency Bill"?

In insurance, **agency bill** means the broker/agent collects the premium from the insured (the law firm) and remits it to the carrier — as opposed to *direct bill*, where the carrier bills the insured directly. This app models the complete lifecycle from an MGA/wholesale broker perspective:

```
Law Firm (insured) → pays premium → Broker/Agent → remits net → Carrier
                                          ↑
                                   retains commission
```

---

## Overview

Eight specialized Claude AI agents handle each phase of the renewal. A master **WorkflowOrchestrator** sequences them, pausing at human-in-the-loop (HITL) decision gates. All state persists in SQLite so workflows can be paused and resumed. A FastAPI web UI provides a live dashboard with HTMX polling, a market submission tracker, an approval queue, and an automated alerts system.

### Workflow Phases

```
Phase 1 — Pre-Renewal Triage      Pull policy, firm, attorney, and claims data
Phase 2 — Renewal Application     Send questionnaire, collect insured information
Phase 3 — Underwriting Review     Risk scoring, loss runs, eligibility decision     ← HITL
Phase 4 — Market Submission       Submit account to wholesale markets for pricing
Phase 5 — Quote Collection        Compare market quotes, accept best option         ← HITL
Phase 6 — Binding & Issuance      Bind coverage, issue binder + declarations        ← HITL
Phase 7 — Invoicing & Payment     Create agency bill invoice, record payment        ← HITL
Phase 8 — Premium Remittance      Remit 85% to carrier, retain 15% commission      ← HITL
```

> **Why market submission instead of internal rating?** Rather than calculating premiums internally, the app behaves like a real wholesale broker: it prepares a submission package, sends it to multiple markets (wholesalers and program administrators), tracks their responses, and lets you enter received quotes as they arrive. This reflects how LPL is actually placed in the surplus lines market.

---

## Setup

### Requirements

- Python 3.11 or later
- An [Anthropic API key](https://console.anthropic.com/)
- No other external services required (SQLite is built-in)

### 1 — Clone and install

```bash
git clone <repo-url>
cd AgencyBill

# Install in editable mode (installs the agencybill CLI)
pip install -e .
```

### 2 — Configure environment

```bash
cp .env.example .env
```

Open `.env` and set your API key:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

All other variables have sensible defaults. See [Configuration](#configuration) for the full list.

### 3 — Seed the database

```bash
agencybill seed
```

This loads 5 sample law firms, 13 attorneys, 2 brokers, 5 LPL policies, claims history, and 10 wholesale markets into a local SQLite database at `data/agencybill.db`.

### 4 — Verify the setup

```bash
# List policies due for renewal
agencybill list-expiring --days 365

# List available wholesale markets
agencybill markets
```

---

## Running the Application

### Web Interface (recommended)

```bash
agencybill web
```

Opens the web dashboard at **http://127.0.0.1:8000**. From there you can:

- Start or resume renewal workflows with a single click
- Monitor live phase progress (HTMX auto-refreshes every 4 seconds)
- Enter market quotes as they arrive from wholesalers
- Approve HITL checkpoints from the **Queue** page
- View and dismiss alerts from the **Alerts** page
- Browse the complete audit trail per workflow

Optional flags:

```bash
agencybill web --host 0.0.0.0 --port 8080   # bind to all interfaces
agencybill web --reload                       # auto-reload on code changes
```

### CLI (interactive, terminal)

```bash
# Start a new renewal workflow — prompts at each HITL checkpoint
agencybill start --policy-id pol-001

# Skip all HITL prompts (automated end-to-end run)
agencybill start --policy-id pol-001 --auto

# Resume a paused workflow
agencybill resume --workflow-id <UUID>
```

At each HITL checkpoint the CLI displays a numbered menu:

```
╔══════════════════════════════════════════════════════╗
║  HUMAN CHECKPOINT — UNDERWRITING                     ║
╠══════════════════════════════════════════════════════╣
║  Underwriting has completed its review. What is      ║
║  the underwriting decision?                          ║
╠══════════════════════════════════════════════════════╣
║  [1] Approve — proceed to market submission          ║
║  [2] Refer — proceed with notation                   ║
║  [3] Decline renewal                                 ║
╚══════════════════════════════════════════════════════╝
Your choice: 1
```

---

## CLI Reference

| Command | Options | Description |
|---|---|---|
| `agencybill seed` | — | Load all sample data into the database |
| `agencybill list-expiring` | `--days N` (default: 90) | Policies expiring within N days |
| `agencybill policies` | — | List all policies |
| `agencybill markets` | `--all` | List wholesale markets (active by default) |
| `agencybill start` | `--policy-id ID`, `--auto` | Start a new renewal workflow |
| `agencybill resume` | `--workflow-id ID`, `--auto` | Resume a paused workflow |
| `agencybill status` | `--workflow-id ID`, `--status-filter S` | Show workflow status |
| `agencybill audit` | `--workflow-id ID` | Print the full agent audit trail |
| `agencybill web` | `--host`, `--port`, `--reload` | Launch the web interface |

---

## Web Interface

The web UI is a FastAPI application rendered server-side with Jinja2 and updated live via [HTMX](https://htmx.org/) — no JavaScript build step required.

### Pages

| URL | Purpose |
|---|---|
| `/` | Dashboard — active workflows, pending queue, expiring policies, stats |
| `/workflows` | All workflows with status filter |
| `/workflows/{id}` | Full workflow detail — status, market submissions, quotes, invoices, alerts, audit trail |
| `/policies` | All policies with expiration color-coding |
| `/queue` | Pending human checkpoints awaiting a decision |
| `/alerts` | Active exceptions and follow-up items (severity filter) |

### Market Quote Entry (web only)

When a market responds with a quote, navigate to the workflow detail page and click **Record Quote** next to the market. Fill in premium, limits, deductible, retroactive date, and any conditions. The agent compares all received quotes and selects the best one automatically when you advance the workflow.

### Live Polling Intervals

| Element | Refresh |
|---|---|
| Workflow phase badge | 4 s |
| Audit trail | 4 s |
| Workflow alerts | 30 s |
| Queue count badge | 10 s |
| Alerts count badge | 30 s |
| Dashboard stats | 15 s |

---

## Market Submission Tracking

Phase 4 submits the account to 3–5 appropriate wholesale markets based on attorney count, state, firm type, practice areas, and premium. Each submission gets:

- A `follow_up_at` deadline (5 days from submission)
- A status that progresses: `submitted → quoted → bound` or `declined / no_response`

As quotes arrive, users enter them through the web UI. The `QuoteCollectionAgent` (Phase 5) then compares all received quotes across premium, limits, deductible, retroactive date, prior acts coverage, and exclusions, selects the best-value option, and notifies the broker.

### Wholesale Markets (seed data)

| Market | Specialty | Attorney Range |
|---|---|---|
| Markel Specialty | LPL / Professional Lines | 1–75 |
| Berkshire Professional | LPL | 1–50 |
| Ironshore Professional | Professional Lines | 5–∞ |
| Hanover Professionals | LPL (Southeast/Midwest) | 1–15 |
| ProAssurance Specialty | LPL / Healthcare | 1–100 |
| ALPS | LPL Program Admin | 1–20 |
| Travelers Specialty | LPL (large firms) | 15–∞ |
| Chubb Professional Risk | High-limits / Complex | 25–∞ |
| Axis Pro | LPL Mid-market | 5–60 |
| Nationwide E&S | E&S / Difficult placements | 1–∞ |

---

## Alerts & Exceptions

The system automatically scans for exception conditions every 60 seconds and surfaces them on the **Alerts** page and as banners on the workflow detail page. Each alert fires once per condition (deduplicated) and auto-resolves when the condition clears.

### Alert Types

| Severity | Type | Trigger |
|---|---|---|
| Critical | `policy_expiring_unbound` | Policy expires ≤ 30 days, renewal not yet bound |
| Critical | `quote_expiring` | Accepted/received quote `valid_through` within 48 hours |
| Critical | `invoice_overdue` | Invoice past due date, still unpaid |
| Critical | `all_markets_declined` | Every submitted market declined or did not respond |
| Critical | `agent_error` | Phase agent threw an exception |
| Warning | `market_followup_overdue` | `follow_up_at` passed, submission still open |
| Warning | `application_not_returned` | Application sent 10+ days ago, not completed |
| Warning | `checkpoint_stale` | Human checkpoint awaiting decision for 3+ days |
| Warning | `workflow_stalled` | Active workflow with no phase change for 5+ days |
| Warning | `remittance_overdue` | Carrier remittance past due date |
| Warning | `invoice_short_pay` | Payment received less than invoice amount |
| Info | `claim_during_renewal` | New claim filed while a renewal workflow is active |

---

## Architecture

```
agencybill/
├── cli.py                  CLI entry point (Click)
├── config.py               Env vars, rating constants, phase definitions
├── database.py             SQLite connection, WAL mode, schema init
├── orchestrator.py         Phase sequencer with HITL gate logic
├── checkpoint.py           Human-in-the-loop — CLI blocking or web DB polling
│
├── agents/
│   ├── base.py             Agentic tool-use loop (Anthropic SDK multi-turn)
│   ├── pre_renewal.py      Phase 1 — Triage
│   ├── application.py      Phase 2 — Application
│   ├── underwriting.py     Phase 3 — Underwriting
│   ├── market_submission.py Phase 4 — Market Submission
│   ├── quote_collection.py Phase 5 — Quote Collection
│   ├── binding.py          Phase 6 — Binding
│   ├── invoicing.py        Phase 7 — Invoicing
│   └── remittance.py       Phase 8 — Remittance
│
├── tools/
│   ├── policy_tools.py     Workflow and policy CRUD
│   ├── firm_tools.py       Law firm and attorney data
│   ├── claims_tools.py     Claims history and loss run generation
│   ├── market_tools.py     Market submissions and quote tracking
│   ├── document_tools.py   Jinja2 document rendering
│   ├── notification_tools.py  Notification log (simulated email)
│   ├── payment_tools.py    Invoices, payments, remittances
│   ├── audit_tools.py      Event logging and checkpoint management
│   └── alerts_tools.py     12-scanner exception detection system
│
├── templates/              Jinja2 HTML output documents
│   ├── renewal_questionnaire.html
│   ├── underwriting_memo.html
│   ├── quote_presentation.html
│   ├── binder.html
│   ├── policy_declarations.html
│   ├── invoice.html
│   └── non_renewal_notice.html
│
├── display/
│   └── console.py          Rich terminal tables and banners
│
└── web/
    ├── app.py              FastAPI application (13 routes + 6 HTMX partials)
    ├── worker.py           Background workflow threads + alert scanner
    └── templates/          Web UI templates (DaisyUI + HTMX)

data/
├── fixtures/               Seed JSON files (firms, policies, markets, etc.)
└── agencybill.db           SQLite database (auto-created at first run)

output/                     Generated HTML documents (per workflow)
```

### How the Agentic Loop Works

Each agent extends `BaseAgent`, which runs a multi-turn tool-use loop via the Anthropic SDK:

```
1. Send system prompt + workflow context to claude-sonnet-4-6
2. Receive response — may contain one or more tool_use blocks
3. Execute each tool call → results written to audit_events
4. Feed tool_result blocks back into the conversation
5. Repeat until stop_reason == "end_turn"
6. Agent returns final summary text to orchestrator
7. Orchestrator advances to next phase or routes to HITL checkpoint
```

### Web Mode vs CLI Mode

In **CLI mode**, HITL checkpoints block the terminal and prompt the user for a numbered choice. In **web mode**, the `WorkflowOrchestrator` runs in a background daemon thread and the checkpoint pauses by polling the `human_checkpoints` DB table every 2 seconds, resuming when the web UI records a decision.

---

## Database Schema

The SQLite database at `data/agencybill.db` has 18 tables:

| Table | Purpose |
|---|---|
| `law_firms` | Insured law firm profiles |
| `attorneys` | Attorney roster (bar number, practice areas) |
| `brokers` | Agent/broker accounts |
| `policies` | LPL policy records (limits, deductibles, premiums) |
| `claims` | Claims history and loss run data |
| `renewal_workflows` | One workflow per renewal cycle |
| `workflow_events` | Phase-level event log |
| `applications` | Renewal questionnaire data |
| `quotes` | Accepted quotes (written by `accept_market_quote`) |
| `markets` | Wholesale market / program admin directory |
| `market_submissions` | Submission per market with status and follow-up deadline |
| `market_quotes` | Quotes received from markets (entered by human) |
| `invoices` | Premium invoices to broker |
| `payments` | Payments received from broker |
| `remittances` | Premium remitted to carrier (85% net) |
| `human_checkpoints` | HITL decisions with full audit |
| `audit_events` | Complete agent action log |
| `notifications` | Notification delivery log |
| `alerts` | Exception/alert records with deduplication key |

---

## Sample Data

Five law firms across multiple states and risk profiles:

| Policy | Firm | State | Type | Premium | Expires |
|---|---|---|---|---|---|
| LPL-2024-00101 | Harrison & Cole LLP | NY | Mid (4 atty) | $48,500 | 2026-07-01 |
| LPL-2024-00202 | Whitfield & Associates | CA | Small (2 atty) | $18,200 | 2026-06-15 |
| LPL-2024-00303 | Ortega Legal Group | TX | Solo (1 atty) | $9,800 | 2026-06-01 |
| LPL-2024-00404 | Mercer Blackwell LLP | IL | Large (4+ atty) | $142,000 | 2026-06-15 |
| LPL-2024-00505 | Caldwell Family Law | KY | Small (2 atty) | $14,600 | 2026-07-01 |

---

## Configuration

All settings are controlled via environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `AGENCYBILL_MODEL` | `claude-sonnet-4-6` | Claude model to use for all agents |
| `AGENCYBILL_DB_PATH` | `data/agencybill.db` | Path to the SQLite database |
| `AGENCYBILL_OUTPUT_DIR` | `output` | Directory for generated HTML documents |
| `AGENCYBILL_MAX_AGENT_TURNS` | `20` | Max tool-use turns per agent phase |

---

## Generated Documents

Each phase produces HTML documents saved to `output/`:

| Phase | Document |
|---|---|
| Application | `renewal_questionnaire_*.html` |
| Underwriting | `underwriting_memo_*.html` |
| Quote Presentation | `quote_presentation_*.html` |
| Binding | `binder_*.html`, `policy_declarations_*.html` |
| Invoicing | `invoice_*.html` |
| Non-renewal | `non_renewal_notice_*.html` |

---

## Non-Renewal & Declination

If the underwriter scores the risk too high, or the human selects "Decline" at the underwriting checkpoint, the orchestrator routes to a non-renewal path:

1. Workflow status set to `non_renewal`
2. `non_renewal_notice.html` generated and saved to `output/`
3. Policy flagged for non-renewal in the database

The same outcome can occur if all markets decline the submission or if the broker/insured declines the selected quote at the Quote Collection checkpoint.

---

## Tech Stack

| Component | Library / Version |
|---|---|
| AI agents | [anthropic](https://github.com/anthropics/anthropic-sdk-python) ≥ 0.40 |
| Model | `claude-sonnet-4-6` (configurable) |
| CLI | [click](https://click.palletsprojects.com/) ≥ 8.1 |
| Terminal UI | [rich](https://github.com/Textualize/rich) ≥ 13 |
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) ≥ 0.110 |
| Web server | [uvicorn](https://www.uvicorn.org/) ≥ 0.29 (with standard extras) |
| Frontend | [HTMX](https://htmx.org/) 1.9 + [DaisyUI](https://daisyui.com/) 4 (CDN) |
| Document templates | [Jinja2](https://jinja.palletsprojects.com/) ≥ 3.1 |
| Database | SQLite 3 (built-in), WAL mode |
| Configuration | [python-dotenv](https://github.com/theskumar/python-dotenv) ≥ 1.0 |
