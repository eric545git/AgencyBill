"""Lightweight SQLite migration runner.

Migrations are idempotent SQL statements applied in order. Schema.sql creates
tables for fresh DBs; migrations evolve existing DBs and add indexes/columns.
"""

from agencybill.logging_config import get_logger

log = get_logger("agencybill.migrations")

# (version, description, sql statements)
MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "indexes + recommended quote + worker status + broker commission",
        [
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT (datetime('now')),
                description TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS worker_status (
                workflow_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                error TEXT,
                started_at TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )""",
            # recommended_market_quote_id — ignore error if already present via try/except in runner
            "ALTER TABLE renewal_workflows ADD COLUMN recommended_market_quote_id TEXT",
            "ALTER TABLE brokers ADD COLUMN commission_pct REAL",
            "CREATE INDEX IF NOT EXISTS idx_workflows_status_phase ON renewal_workflows(status, phase)",
            "CREATE INDEX IF NOT EXISTS idx_policies_exp_status ON policies(expiration_date, status)",
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_decision ON human_checkpoints(decision)",
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_workflow ON human_checkpoints(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_alerts_status_sev ON alerts(status, severity)",
            "CREATE INDEX IF NOT EXISTS idx_audit_workflow ON audit_events(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_submissions_workflow ON market_submissions(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_market_quotes_workflow ON market_quotes(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_quotes_workflow ON quotes(workflow_id)",
        ],
    ),
]


def apply_migrations(conn) -> None:
    """Apply pending migrations on an open connection."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now')),
            description TEXT
        )"""
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    current = row[0] if row else 0

    for version, description, statements in MIGRATIONS:
        if version <= current:
            continue
        log.info("Applying migration %s: %s", version, description)
        for sql in statements:
            try:
                conn.execute(sql)
            except Exception as exc:
                # ALTER TABLE ADD COLUMN fails if column exists — safe to ignore
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                # schema_migrations create is redundant in migration body
                if "schema_migrations" in sql and "already exists" in msg:
                    continue
                raise
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
            (version, description),
        )
        log.info("Migration %s applied", version)
