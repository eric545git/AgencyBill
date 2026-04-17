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
