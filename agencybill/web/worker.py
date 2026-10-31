"""Background workflow runner for the web interface."""

import threading
import traceback
from datetime import datetime

from agencybill.database import db
from agencybill.tools.audit_tools import log_audit_event
from agencybill.logging_config import get_logger

log = get_logger("agencybill.worker")

# workflow_id → {"thread", "status", "error", "started_at"}
_registry: dict[str, dict] = {}
_lock = threading.Lock()

_ALERT_SCAN_INTERVAL = 60  # seconds between alert scans
_scanner_thread: threading.Thread | None = None


def _persist_status(workflow_id: str, status: str, error: str | None = None,
                    started_at: str | None = None) -> None:
    """Persist worker status so it survives process inspection / restarts."""
    with db() as conn:
        conn.execute(
            """INSERT INTO worker_status (workflow_id, status, error, started_at, updated_at)
               VALUES (?, ?, ?, COALESCE(?, datetime('now')), datetime('now'))
               ON CONFLICT(workflow_id) DO UPDATE SET
                 status = excluded.status,
                 error = excluded.error,
                 started_at = COALESCE(excluded.started_at, worker_status.started_at),
                 updated_at = datetime('now')""",
            (workflow_id, status, error, started_at),
        )


def _load_persisted(workflow_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM worker_status WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
    return dict(row) if row else {}


# ─────────────────────────────────────────────────────────────────────────────
# ALERT SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def _alert_scanner_loop() -> None:
    import time
    from agencybill.tools.alerts_tools import run_alert_scan
    while True:
        try:
            run_alert_scan()
        except Exception:
            log.exception("Alert scanner cycle failed")
        time.sleep(_ALERT_SCAN_INTERVAL)


def ensure_alert_scanner() -> None:
    """Start the alert scanner thread if not already running."""
    global _scanner_thread
    if _scanner_thread and _scanner_thread.is_alive():
        return
    _scanner_thread = threading.Thread(
        target=_alert_scanner_loop, daemon=True, name="alert-scanner"
    )
    _scanner_thread.start()
    log.info("Alert scanner started (interval=%ss)", _ALERT_SCAN_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW RUNNER
# ─────────────────────────────────────────────────────────────────────────────


def get_status(workflow_id: str) -> dict:
    with _lock:
        live = dict(_registry.get(workflow_id, {}))
    if live:
        # Don't expose the Thread object to callers
        return {k: v for k, v in live.items() if k != "thread"}
    return _load_persisted(workflow_id)


def list_running() -> list[str]:
    with _lock:
        running = [wid for wid, info in _registry.items()
                   if info.get("status") == "running"]
    if running:
        return running
    with db() as conn:
        rows = conn.execute(
            "SELECT workflow_id FROM worker_status WHERE status = 'running'"
        ).fetchall()
    # After restart, persisted "running" is stale — treat as unknown, not running
    return []


def start_workflow(policy_id: str, workflow_id: str) -> None:
    """Launch a workflow in a background thread (web mode, with HITL via DB)."""
    with _lock:
        existing = _registry.get(workflow_id, {})
        if existing.get("status") == "running":
            return

    started_at = datetime.now().isoformat()

    def _run():
        _set(workflow_id, "running", started_at=started_at)
        try:
            from agencybill.orchestrator import WorkflowOrchestrator
            orch = WorkflowOrchestrator(
                workflow_id, policy_id, auto_mode=False, web_mode=True
            )
            orch.run()
            _set(workflow_id, "done")
        except Exception as exc:
            log.exception("Background workflow %s failed", workflow_id)
            _set(workflow_id, "error", error=traceback.format_exc())
            log_audit_event(workflow_id, "BackgroundWorker", "worker_error",
                            {"error": str(exc)})

    t = threading.Thread(target=_run, daemon=True, name=f"wf-{workflow_id[:8]}")
    with _lock:
        _registry[workflow_id] = {
            "thread": t,
            "status": "running",
            "error": None,
            "started_at": started_at,
        }
    _persist_status(workflow_id, "running", started_at=started_at)
    t.start()


def _set(workflow_id: str, status: str, error: str = None,
         started_at: str = None) -> None:
    with _lock:
        entry = _registry.setdefault(workflow_id, {})
        entry["status"] = status
        if error:
            entry["error"] = error
        if started_at:
            entry["started_at"] = started_at
    _persist_status(workflow_id, status, error=error, started_at=started_at)
