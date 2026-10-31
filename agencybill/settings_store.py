"""Small key/value settings stored in SQLite.

The OpenRouter API key and model id saved from the web Admin page live here.
A saved key overrides ``OPENROUTER_API_KEY``. A saved model overrides
``AGENCYBILL_MODEL``. Clearing either value falls back to the environment.
Values are never logged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

OPENROUTER_API_KEY_SETTING = "openrouter_api_key"
LLM_MODEL_SETTING = "llm_model"
_MAX_KEY_LENGTH = 512
_MAX_MODEL_LENGTH = 200


def mask_api_key(api_key: str | None) -> str:
    """Return ``not set`` or a last-four hint. Never the full key."""
    value = (api_key or "").strip()
    if not value:
        return "not set"
    if len(value) <= 8:
        return "••••"
    hint = f"••••{value[-4:]}"
    if hint == value:
        return "••••"
    return hint


def get_openrouter_api_key_override() -> str | None:
    """Non-empty saved OpenRouter key, or None when the DB has no override.

    None covers a missing database file, a database that has not been migrated
    yet, a missing row, and a blank value. Callers then use the env var.
    """
    return _get_setting(OPENROUTER_API_KEY_SETTING)


def get_model_override() -> str | None:
    """Non-empty saved model id, or None so callers use ``AGENCYBILL_MODEL``."""
    return _get_setting(LLM_MODEL_SETTING)


def set_openrouter_api_key(api_key: str) -> None:
    """Replace the saved OpenRouter key. Blank input is rejected."""
    value = (api_key or "").strip()
    if not value:
        raise ValueError("OpenRouter API key cannot be blank")
    if len(value) > _MAX_KEY_LENGTH:
        raise ValueError("OpenRouter API key is too long")
    _set_setting(OPENROUTER_API_KEY_SETTING, value)


def clear_openrouter_api_key() -> None:
    """Remove the saved key so routing falls back to the environment."""
    _delete_setting(OPENROUTER_API_KEY_SETTING)


def set_model(model: str) -> None:
    """Replace the saved model id. Blank input is rejected."""
    value = (model or "").strip()
    if not value:
        raise ValueError("Model cannot be blank")
    if any(ch.isspace() for ch in value):
        raise ValueError("Model id cannot contain spaces")
    if len(value) > _MAX_MODEL_LENGTH:
        raise ValueError("Model id is too long")
    _set_setting(LLM_MODEL_SETTING, value)


def clear_model() -> None:
    """Remove the saved model so calls fall back to ``AGENCYBILL_MODEL``."""
    _delete_setting(LLM_MODEL_SETTING)


def _get_setting(key: str) -> str | None:
    """Non-empty saved value, or None when there is no override."""
    conn = _connect_readonly()
    if conn is None:
        return None
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    value = (row["value"] or "").strip()
    return value or None


def _set_setting(key: str, value: str) -> None:
    from agencybill.database import db

    with db() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = datetime('now')""",
            (key, value),
        )


def _delete_setting(key: str) -> None:
    from agencybill.database import db

    with db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def _connect_readonly() -> sqlite3.Connection | None:
    """Open the app database when the file already exists.

    Skipping a missing file avoids creating an empty database just to discover
    that no key has been saved. The connection matches the rest of the app
    (WAL, row factory) so a settings read sees committed writes.
    """
    from agencybill.database import DB_PATH, get_connection

    if not Path(DB_PATH).is_file():
        return None
    return get_connection()
