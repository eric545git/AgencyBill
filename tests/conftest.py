"""Shared pytest fixtures — isolated temp SQLite DB per test."""

import os
import json
from pathlib import Path

import pytest

# Point DB at a temp file before agencybill modules open connections
@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AGENCYBILL_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENCYBILL_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("AGENCYBILL_WEB_PASSWORD", "")

    # Reload config/database paths for this process
    import agencybill.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(cfg, "WEB_PASSWORD", "")
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    import agencybill.database as database
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.init_db()
    _seed_minimal()
    yield db_path


def _seed_minimal():
    from agencybill.database import db

    with db() as conn:
        conn.execute(
            """INSERT INTO brokers (id, name, license_number, state, email, agency_name, commission_pct)
               VALUES ('brk-1', 'Test Broker', 'LIC-1', 'NY', 'broker@example.com', 'Test Agency', 0.12)"""
        )
        conn.execute(
            """INSERT INTO law_firms
               (id, name, state, firm_type, email, year_founded)
               VALUES ('firm-1', 'Test Firm LLP', 'NY', 'small', 'firm@example.com', 2001)"""
        )
        conn.execute(
            """INSERT INTO attorneys
               (id, firm_id, name, practice_areas, years_admitted, active)
               VALUES ('atty-1', 'firm-1', 'Ada Attorney', ?, 10, 1)""",
            (json.dumps(["corporate", "litigation"]),),
        )
        conn.execute(
            """INSERT INTO policies
               (id, firm_id, broker_id, policy_number, carrier,
                effective_date, expiration_date, limit_per_claim, aggregate_limit,
                deductible, annual_premium, status, attorney_count)
               VALUES ('pol-1', 'firm-1', 'brk-1', 'POL-001', 'Test Carrier',
                       '2025-01-01', '2026-01-01', 1000000, 1000000,
                       5000, 25000, 'active', 2)"""
        )
        conn.execute(
            """INSERT INTO markets
               (id, name, contact_email, specialty, states, min_attorneys, active)
               VALUES ('mkt-1', 'Test Market', 'mkt@example.com', 'LPL', ?, 1, 1)""",
            (json.dumps(["NY", "CA"]),),
        )
