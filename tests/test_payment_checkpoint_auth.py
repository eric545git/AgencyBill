"""Commission resolution, checkpoint normalization, auth, migrations, static."""

from pathlib import Path

from agencybill.checkpoint import normalize_options
from agencybill.tools.payment_tools import resolve_commission_pct, create_remittance
from agencybill.config import DEFAULT_COMMISSION_PCT, WEB_PASSWORD
from agencybill.database import db, init_db


def test_normalize_options_strings_and_dicts():
    opts = normalize_options([
        "plain",
        {"key": "confirm", "label": "Confirm bind"},
        {"key": "hold", "label": "Hold"},
    ])
    assert opts[0] == {"key": "plain", "label": "plain"}
    assert opts[1]["key"] == "confirm"
    assert opts[2]["key"] == "hold"


def test_commission_from_broker_override(tmp_db):
    resolved = resolve_commission_pct("brk-1")
    assert resolved["commission_pct"] == 0.12
    assert resolved["source"] == "broker"

    defaulted = resolve_commission_pct(None)
    assert defaulted["commission_pct"] == DEFAULT_COMMISSION_PCT
    assert defaulted["source"] == "default"


def test_create_remittance_uses_broker_commission(tmp_db):
    from agencybill.tools.policy_tools import get_or_create_workflow
    wf = get_or_create_workflow("pol-1")
    rem = create_remittance(wf["id"], "Carrier", 10000.0, broker_id="brk-1")
    assert rem["commission_pct"] == 0.12
    assert rem["net_commission"] == 1200.0
    assert rem["carrier_amount"] == 8800.0


def test_migrations_applied(tmp_db):
    with db() as conn:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()
        assert row["v"] >= 1
        # recommended column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(renewal_workflows)").fetchall()]
        assert "recommended_market_quote_id" in cols
        # indexes exist
        idxs = [r[1] for r in conn.execute("PRAGMA index_list(policies)").fetchall()]
        assert any("exp_status" in i for i in idxs)


def test_static_dir_exists():
    static = Path(__file__).resolve().parents[1] / "agencybill" / "web" / "static"
    assert static.is_dir()


def test_web_auth_dependency_open_without_password(tmp_db):
    """When WEB_PASSWORD is empty, _require_auth allows anonymous access."""
    from agencybill.web.app import _require_auth
    # Depends() default when no credentials and empty password
    assert _require_auth(None) is None


def test_web_auth_rejects_bad_credentials(tmp_db, monkeypatch):
    import agencybill.config as cfg
    import agencybill.web.app as webapp
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials

    monkeypatch.setattr(cfg, "WEB_PASSWORD", "secret")
    monkeypatch.setattr(cfg, "WEB_USER", "agencybill")
    monkeypatch.setattr(webapp, "WEB_PASSWORD", "secret")
    monkeypatch.setattr(webapp, "WEB_USER", "agencybill")

    try:
        webapp._require_auth(HTTPBasicCredentials(username="agencybill", password="wrong"))
        assert False, "expected 401"
    except HTTPException as exc:
        assert exc.status_code == 401

    user = webapp._require_auth(
        HTTPBasicCredentials(username="agencybill", password="secret")
    )
    assert user == "agencybill"
