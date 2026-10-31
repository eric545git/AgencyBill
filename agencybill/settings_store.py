"""Small key/value settings stored in SQLite.

The OpenRouter API key saved from the web Admin page lives here. It overrides
the ``OPENROUTER_API_KEY`` environment variable until an operator clears it.
Values are never logged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

OPENROUTER_API_KEY_SETTING = "openrouter_api_key"
_MAX_KEY_LENGTH = 512


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
            (OPENROUTER_API_KEY_SETTING,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    value = (row["value"] or "").strip()
    return value or None


def set_openrouter_api_key(api_key: str) -> None:
    """Replace the saved OpenRouter key. Blank input is rejected."""
    value = (api_key or "").strip()
    if not value:
        raise ValueError("OpenRouter API key cannot be blank")
    if len(value) > _MAX_KEY_LENGTH:
        raise ValueError("OpenRouter API key is too long")
    from agencybill.database import db

    with db() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = datetime('now')""",
            (OPENROUTER_API_KEY_SETTING, value),
        )


def clear_openrouter_api_key() -> None:
    """Remove the saved key so routing falls back to the environment."""
    from agencybill.database import db

    with db() as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = ?",
            (OPENROUTER_API_KEY_SETTING,),
        )


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
