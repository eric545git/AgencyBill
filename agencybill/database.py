"""SQLite database connection and schema initialisation."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

from agencybill.config import DB_PATH

_schema_path = Path(__file__).parent / "db" / "schema.sql"


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with row_factory set."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db():
    """Context manager yielding a connection that auto-commits or rolls back."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    sql = _schema_path.read_text()
    with db() as conn:
        conn.executescript(sql)


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row else {}
