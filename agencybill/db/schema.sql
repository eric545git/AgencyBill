PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ─────────────────────────────────────────────
-- FIRMS & ATTORNEYS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS law_firms (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tax_id          TEXT,
    address_line1   TEXT,
    city            TEXT,
    state           TEXT NOT NULL,
    zip             TEXT,
    phone           TEXT,
    email           TEXT,
    firm_type       TEXT CHECK(firm_type IN ('solo','small','mid','large')),
    year_founded    INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attorneys (
    id              TEXT PRIMARY KEY,
    firm_id         TEXT NOT NULL REFERENCES law_firms(id),
    name            TEXT NOT NULL,
    bar_number      TEXT,
    state_licensed  TEXT,
    practice_areas  TEXT,   -- JSON array of practice area strings
    years_admitted  INTEGER,
    active          INTEGER DEFAULT 1
);

-- ─────────────────────────────────────────────
-- BROKERS / AGENTS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS brokers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    license_number  TEXT,
    state           TEXT,
    email           TEXT,
    phone           TEXT,
    agency_name     TEXT
);

-- ─────────────────────────────────────────────
-- POLICIES
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS policies (
    id                  TEXT PRIMARY KEY,
    firm_id             TEXT NOT NULL REFERENCES law_firms(id),
    broker_id           TEXT REFERENCES brokers(id),
    policy_number       TEXT UNIQUE,
    carrier             TEXT NOT NULL,
    effective_date      TEXT NOT NULL,   -- ISO date
    expiration_date     TEXT NOT NULL,   -- ISO date
    limit_per_claim     INTEGER NOT NULL,
    aggregate_limit     INTEGER NOT NULL,
    deductible          INTEGER NOT NULL DEFAULT 2500,
    annual_premium      REAL NOT NULL,
    status              TEXT DEFAULT 'active'
        CHECK(status IN ('active','expired','cancelled','non-renewed')),
    attorney_count      INTEGER,
    created_at          TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- CLAIMS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS claims (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT NOT NULL REFERENCES policies(id),
    firm_id         TEXT NOT NULL REFERENCES law_firms(id),
    claim_number    TEXT,
    claim_date      TEXT NOT NULL,
    closed_date     TEXT,
    claim_type      TEXT,
    practice_area   TEXT,
    amount_paid     REAL DEFAULT 0,
    amount_reserved REAL DEFAULT 0,
    status          TEXT DEFAULT 'open'
        CHECK(status IN ('open','closed','reserved','dismissed')),
    description     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- RENEWAL WORKFLOWS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS renewal_workflows (
    id                  TEXT PRIMARY KEY,
    policy_id           TEXT NOT NULL REFERENCES policies(id),
    phase               TEXT NOT NULL DEFAULT 'PRE_RENEWAL',
    status              TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','paused','complete','non_renewal','declined','cancelled')),
    renewal_year        INTEGER,
    started_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    completed_at        TEXT,
    assigned_underwriter TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES renewal_workflows(id),
    phase       TEXT,
    event_type  TEXT NOT NULL,
    actor       TEXT,
    data_json   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- APPLICATIONS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS applications (
    id                      TEXT PRIMARY KEY,
    workflow_id             TEXT NOT NULL REFERENCES renewal_workflows(id),
    sent_at                 TEXT,
    received_at             TEXT,
    attorney_count          INTEGER,
    gross_revenue           REAL,
    practice_areas_json     TEXT,   -- JSON array
    prior_acts_coverage     INTEGER DEFAULT 1,
    claims_history_json     TEXT,   -- JSON array of disclosed claims
    claims_history_disclosed INTEGER DEFAULT 0,
    additional_notes        TEXT,
    completed               INTEGER DEFAULT 0,
    created_at              TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- QUOTES
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quotes (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL REFERENCES renewal_workflows(id),
    option_label        TEXT,   -- e.g. "Option A", "Option B"
    per_claim_limit     INTEGER,
    aggregate_limit     INTEGER,
    deductible          INTEGER,
    annual_premium      REAL,
    base_premium        REAL,
    experience_mod      REAL DEFAULT 1.0,
    practice_area_mod   REAL DEFAULT 1.0,
    revenue_mod         REAL DEFAULT 1.0,
    deductible_credit   REAL DEFAULT 0.0,
    status              TEXT DEFAULT 'draft'
        CHECK(status IN ('draft','presented','accepted','declined','countered','expired')),
    generated_at        TEXT DEFAULT (datetime('now')),
    presented_at        TEXT,
    accepted_at         TEXT,
    expires_at          TEXT,
    notes               TEXT
);

-- ─────────────────────────────────────────────
-- INVOICES, PAYMENTS, REMITTANCES
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES renewal_workflows(id),
    invoice_number  TEXT UNIQUE,
    amount_due      REAL NOT NULL,
    issued_at       TEXT DEFAULT (datetime('now')),
    due_date        TEXT,
    paid_at         TEXT,
    status          TEXT DEFAULT 'unpaid'
        CHECK(status IN ('unpaid','paid','partial','overdue','void'))
);

CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,
    invoice_id      TEXT NOT NULL REFERENCES invoices(id),
    amount          REAL NOT NULL,
    received_at     TEXT DEFAULT (datetime('now')),
    method          TEXT DEFAULT 'check'
        CHECK(method IN ('check','wire','ach','credit_card')),
    reference       TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS remittances (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES renewal_workflows(id),
    carrier         TEXT NOT NULL,
    amount          REAL NOT NULL,
    net_commission  REAL DEFAULT 0,
    remitted_at     TEXT,
    due_date        TEXT,
    reference_number TEXT,
    status          TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','remitted','overdue'))
);

-- ─────────────────────────────────────────────
-- MARKET SUBMISSIONS & QUOTES
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS markets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    specialty       TEXT,           -- e.g. "LPL", "Professional Lines"
    appetite_notes  TEXT,           -- what risks they prefer
    states          TEXT,           -- JSON array of states they write, empty = all
    min_attorneys   INTEGER DEFAULT 1,
    max_attorneys   INTEGER,
    min_premium     REAL,
    max_premium     REAL,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_submissions (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL REFERENCES renewal_workflows(id),
    market_id           TEXT NOT NULL REFERENCES markets(id),
    submitted_at        TEXT DEFAULT (datetime('now')),
    submission_notes    TEXT,
    status              TEXT DEFAULT 'submitted'
        CHECK(status IN ('submitted','quoted','declined','passed','bound','no_response')),
    follow_up_at        TEXT,
    last_contact_at     TEXT,
    declination_reason  TEXT
);

CREATE TABLE IF NOT EXISTS market_quotes (
    id                  TEXT PRIMARY KEY,
    submission_id       TEXT NOT NULL REFERENCES market_submissions(id),
    workflow_id         TEXT NOT NULL REFERENCES renewal_workflows(id),
    market_id           TEXT NOT NULL REFERENCES markets(id),
    received_at         TEXT DEFAULT (datetime('now')),
    quote_number        TEXT,
    carrier             TEXT,
    per_claim_limit     INTEGER,
    aggregate_limit     INTEGER,
    deductible          INTEGER,
    annual_premium      REAL,
    effective_date      TEXT,
    expiration_date     TEXT,
    retroactive_date    TEXT,
    prior_acts          INTEGER DEFAULT 1,
    exclusions_json     TEXT,       -- JSON array of exclusion strings
    conditions_json     TEXT,       -- JSON array of condition strings
    valid_through       TEXT,
    status              TEXT DEFAULT 'received'
        CHECK(status IN ('received','presented','accepted','declined','expired')),
    notes               TEXT,
    entered_by          TEXT DEFAULT 'human',
    entered_at          TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- HUMAN CHECKPOINTS & AUDIT
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS human_checkpoints (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES renewal_workflows(id),
    phase       TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    options_json TEXT,
    decision    TEXT,
    decided_by  TEXT DEFAULT 'human',
    decided_at  TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT,
    agent_name  TEXT,
    action      TEXT NOT NULL,
    details_json TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT,
    recipient   TEXT,
    subject     TEXT,
    body        TEXT,
    channel     TEXT DEFAULT 'email',
    sent_at     TEXT DEFAULT (datetime('now')),
    status      TEXT DEFAULT 'sent'
);

-- ─────────────────────────────────────────────
-- ALERTS & EXCEPTIONS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    id              TEXT PRIMARY KEY,
    alert_type      TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK(severity IN ('critical','warning','info')),
    workflow_id     TEXT REFERENCES renewal_workflows(id),
    entity_type     TEXT,               -- 'workflow','submission','invoice','quote','policy','claim'
    entity_id       TEXT,
    title           TEXT NOT NULL,
    detail          TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open','acknowledged','resolved','snoozed')),
    dedup_key       TEXT UNIQUE,        -- prevents duplicate alerts for the same condition
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    resolved_at     TEXT,
    resolved_by     TEXT
);
