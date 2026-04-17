"""Background workflow runner for the web interface."""

import threading
import traceback
from datetime import datetime
from typing import Literal

from agencybill.database import db
from agencybill.tools.audit_tools import log_audit_event

# workflow_id → {"thread", "status", "error", "started_at"}
_registry: dict[str, dict] = {}
_lock = threading.Lock()

_ALERT_SCAN_INTERVAL = 60  # seconds between alert scans
_scanner_thread: threading.Thread | None = None


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
            pass  # never crash the scanner thread
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


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW RUNNER
# ─────────────────────────────────────────────────────────────────────────────


def get_status(workflow_id: str) -> dict:
    with _lock:
        return dict(_registry.get(workflow_id, {}))


def list_running() -> list[str]:
    with _lock:
        return [wid for wid, info in _registry.items()
                if info.get("status") == "running"]


def start_workflow(policy_id: str, workflow_id: str) -> None:
    """Launch a workflow in a background thread (web mode, no HITL blocking)."""
    with _lock:
        existing = _registry.get(workflow_id, {})
        if existing.get("status") == "running":
            return  # already running

    def _run():
        _set(workflow_id, "running")
        try:
            from agencybill.orchestrator import WorkflowOrchestrator
            orch = WorkflowOrchestrator(
                workflow_id, policy_id, auto_mode=False, web_mode=True
            )
            orch.run()
            _set(workflow_id, "done")
        except Exception as exc:
            _set(workflow_id, "error", error=traceback.format_exc())
            log_audit_event(workflow_id, "BackgroundWorker", "worker_error",
                            {"error": str(exc)})

    t = threading.Thread(target=_run, daemon=True, name=f"wf-{workflow_id[:8]}")
    with _lock:
        _registry[workflow_id] = {
            "thread": t,
            "status": "running",
            "error": None,
            "started_at": datetime.now().isoformat(),
        }
    t.start()


def _set(workflow_id: str, status: str, error: str = None) -> None:
    with _lock:
        entry = _registry.setdefault(workflow_id, {})
        entry["status"] = status
        if error:
            entry["error"] = error
