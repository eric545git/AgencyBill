# AgencyBill — Developer Guide

This file provides codebase context for AI assistants and developers working on this project.

## What This App Does

AgencyBill automates LPL (Lawyers Professional Liability) insurance renewals using the **agency bill** model: the broker collects premium from the law firm and remits net to the carrier, retaining a commission. Eight specialized Claude agents run sequentially, each responsible for one phase of the renewal cycle.

## Running the App

```bash
pip install -e .
cp .env.example .env        # add ANTHROPIC_API_KEY
agencybill seed             # load sample data
agencybill web              # start web UI at http://127.0.0.1:8000
agencybill start --policy-id pol-001 --auto   # CLI, no HITL prompts
```

## Key Architectural Decisions

### Agent Structure

Every agent inherits `BaseAgent` (`agencybill/agents/base.py`). The base class runs a multi-turn Anthropic SDK tool-use loop: it sends a system prompt + user message, receives tool_use blocks, executes each tool, feeds back tool_result blocks, and loops until `stop_reason == "end_turn"`. All tool calls are logged to `audit_events`.

Agents register their tools in `_register_tools()` using `_add_tool(name, description, input_schema, callable)`. The callable is a plain Python function from one of the `tools/` modules.

Each agent exposes `run_phase(policy_id, workflow_id) -> str` which is called by the orchestrator.

### Web Mode vs CLI Mode

The `WorkflowOrchestrator` accepts `web_mode=bool`. In web mode:
- Workflows run in daemon threads managed by `agencybill/web/worker.py`
- `HumanCheckpoint` writes a checkpoint record to the DB and **polls every 2 seconds** until a decision is recorded via the web UI
- In CLI mode, the checkpoint blocks the terminal with a Rich-formatted prompt

### Phase Sequencing

`orchestrator.py` owns `AGENT_MAP` (phase name → agent class), `CHECKPOINTS_BEFORE` (phases requiring approval before the agent runs), and `CHECKPOINTS_AFTER` (phases requiring human review of agent output). When adding a new phase, update all three structures plus `config.py PHASES`.

### Market Submission Flow (Phases 4–5)

Phase 4 (`MarketSubmissionAgent`) selects markets from the `markets` table based on attorney count, state, premium, and practice areas, creates a `market_submissions` record per market with a `follow_up_at` deadline, and sends simulated submission emails.

Phase 5 (`QuoteCollectionAgent`) reads received quotes from `market_quotes` (entered by humans via the web UI `POST /workflows/{id}/record-quote`), compares them, calls `accept_market_quote()` on the winner, and the `quotes` table record written there is what downstream agents (`BindingAgent`, `InvoicingAgent`) read.

### Alert System

`agencybill/tools/alerts_tools.py` contains 12 independent scanner functions. Each scanner queries the DB for a specific condition, calls `_upsert_alert()` with a `dedup_key` (prevents duplicate alerts), and calls `_auto_resolve()` when the condition clears. `run_alert_scan()` calls all 12 in sequence.

The scanner runs every 60 seconds in a daemon thread started by `worker.ensure_alert_scanner()` at web app startup. It also runs on-demand when the `/alerts` page is loaded.

### Database

SQLite with WAL mode and foreign keys enabled. All access goes through the `db()` context manager in `agencybill/database.py`, which returns a `sqlite3.Connection` with `row_factory = sqlite3.Row`. `row_to_dict(row)` converts rows to plain dicts.

Schema is in `agencybill/db/schema.sql` and applied via `init_db()` on startup. All tables use `CREATE TABLE IF NOT EXISTS` so re-running `init_db()` is safe.

### Notification System

`send_notification()` writes to the `notifications` table — it does **not** actually send email. In a production deployment this would call an email API. Agents use it to simulate outbound communications.

## File Map

```
agencybill/
  cli.py                  Click CLI: seed, list-expiring, policies, markets,
                          start, resume, status, audit, web
  config.py               PHASES list, PHASE_LABELS, rating constants
  database.py             init_db(), db() context manager, row_to_dict()
  orchestrator.py         WorkflowOrchestrator — AGENT_MAP, CHECKPOINTS_BEFORE/AFTER
  checkpoint.py           HumanCheckpoint — CLI blocking or web DB polling

  agents/
    base.py               BaseAgent: _add_tool(), run(), _run_loop()
    pre_renewal.py        Phase 1
    application.py        Phase 2
    underwriting.py       Phase 3
    market_submission.py  Phase 4
    quote_collection.py   Phase 5
    binding.py            Phase 6
    invoicing.py          Phase 7
    remittance.py         Phase 8

  tools/
    policy_tools.py       list_expiring_policies, read_policy, read_workflow,
                          get_or_create_workflow, update_workflow_phase,
                          update_workflow_status, list_workflows, log_workflow_event
    firm_tools.py         read_firm, read_attorneys, get_firm_practice_areas, read_broker
    claims_tools.py       get_claims_for_policy, generate_loss_run,
                          compute_experience_modifier, assess_risk
    market_tools.py       list_markets, select_markets_for_account,
                          create_submission, get_submissions, update_submission_status,
                          record_market_quote, get_market_quotes, accept_market_quote
    document_tools.py     render_template, save_document (Jinja2 → output/)
    notification_tools.py send_notification, get_notifications
    payment_tools.py      create_invoice, record_payment, create_remittance,
                          mark_remittance_sent, get_invoices_for_workflow, get_remittances
    audit_tools.py        log_audit_event, get_audit_trail,
                          create_checkpoint, resolve_checkpoint, get_checkpoints
    alerts_tools.py       run_alert_scan, get_alerts, count_open_alerts,
                          acknowledge_alert, resolve_alert, get_alerts_for_workflow

  db/
    schema.sql            18 tables — law_firms, attorneys, brokers, policies,
                          claims, renewal_workflows, workflow_events, applications,
                          quotes, markets, market_submissions, market_quotes,
                          invoices, payments, remittances, human_checkpoints,
                          audit_events, notifications, alerts

  web/
    app.py                FastAPI app — routes, Jinja2 templates, HTMX partials
    worker.py             _registry (workflow threads), ensure_alert_scanner()
    templates/
      base.html           Nav (Dashboard, Workflows, Policies, Queue, Alerts)
      dashboard.html
      workflows.html
      workflow_detail.html  Market submissions panel, quote comparison, alerts
      policies.html
      queue.html
      alerts.html
      partials/
        workflow_status.html
        audit_rows.html
        workflow_alerts.html
        dashboard_stats.html

  templates/              Jinja2 HTML document output templates
    renewal_questionnaire.html
    underwriting_memo.html
    quote_presentation.html
    binder.html
    policy_declarations.html
    invoice.html
    non_renewal_notice.html

data/
  fixtures/               Seed JSON: seed_firms, seed_attorneys, seed_brokers,
                          seed_policies, seed_claims, seed_markets
  agencybill.db           SQLite DB (gitignored, auto-created)

output/                   Generated HTML documents (gitignored)
```

## Adding a New Agent Phase

1. Create `agencybill/agents/my_phase.py` extending `BaseAgent`
2. Add the phase name to `PHASES` in `config.py` and `PHASE_LABELS`
3. Add the agent class to `AGENT_MAP` in `orchestrator.py`
4. Add any HITL checkpoints to `CHECKPOINTS_BEFORE` or `CHECKPOINTS_AFTER`
5. If a HITL checkpoint needs special decision logic, add an `elif phase == "MY_PHASE"` block in the `orchestrator.run()` loop
6. Update the phase percentage lookup in `app.py _phase_pct()`
7. Update `base.html` CSS for the new phase pill class

## Adding a New Alert Type

1. Write a `_scan_my_condition()` function in `alerts_tools.py`
   - Query the DB for rows matching the condition
   - For each match: call `_upsert_alert(...)` with a stable `dedup_key`
   - Call `_auto_resolve(key)` when the condition no longer exists
2. Call it inside `run_alert_scan()`

The `dedup_key` format is `"alert_type:entity_id"`. Never include timestamps in the dedup key — the same alert should map to the same key every time it's checked.

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | No fallback |
| `AGENCYBILL_MODEL` | `claude-sonnet-4-6` | Change to `claude-opus-4-7` for more capable agents |
| `AGENCYBILL_DB_PATH` | `data/agencybill.db` | Relative to project root |
| `AGENCYBILL_OUTPUT_DIR` | `output` | Relative to project root |
| `AGENCYBILL_MAX_AGENT_TURNS` | `20` | Increase if complex accounts run out of turns |

## Common Issues

**`ModuleNotFoundError: agencybill`** — Run `pip install -e .` from the project root.

**`ANTHROPIC_API_KEY` not found** — Ensure `.env` exists and has the key. `config.py` calls `load_dotenv()` which reads `.env` relative to the working directory.

**Workflow stuck at a HITL checkpoint in web mode** — Navigate to `/queue` in the browser and submit a decision. The background thread polls the DB and will resume within 2 seconds.

**Agent runs out of turns** — Increase `AGENCYBILL_MAX_AGENT_TURNS` in `.env`. Default is 20; complex accounts with many markets may need 30+.

**Database locked errors** — WAL mode is enabled but the DB file can still be locked if a previous run crashed mid-write. Delete `data/agencybill.db` and re-run `agencybill seed`.

**Stale alerts not auto-resolving** — The scanner runs every 60 seconds in the background. Navigate to `/alerts` to trigger an immediate scan, or wait for the next cycle.
