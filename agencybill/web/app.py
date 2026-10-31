"""FastAPI web application for AgencyBill."""

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agencybill.config import PHASES, WEB_PASSWORD, WEB_USER, phase_pct
from agencybill.database import init_db, db, row_to_dict
from agencybill.logging_config import get_logger
from agencybill.tools.policy_tools import (
    read_policy, list_expiring_policies, list_workflows,
    read_workflow, get_or_create_workflow,
)
from agencybill.tools.audit_tools import (
    get_audit_trail, get_checkpoints, resolve_checkpoint,
)
from agencybill.tools.payment_tools import get_invoices_for_workflow, get_remittances
from agencybill.tools.notification_tools import get_notifications
from agencybill.tools.market_tools import (
    get_submissions, get_market_quotes, record_market_quote,
)
from agencybill.tools.alerts_tools import (
    get_alerts, count_open_alerts, acknowledge_alert, resolve_alert,
    get_alerts_for_workflow, run_alert_scan,
)
from agencybill.web import worker

log = get_logger("agencybill.web")

_HERE = Path(__file__).parent
_STATIC = _HERE / "static"
_STATIC.mkdir(parents=True, exist_ok=True)

_security = HTTPBasic(auto_error=False)


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)):
    """Enforce HTTP Basic Auth when AGENCYBILL_WEB_PASSWORD is set."""
    if not WEB_PASSWORD:
        return None
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="AgencyBill"'},
        )
    user_ok = secrets.compare_digest(credentials.username, WEB_USER)
    pass_ok = secrets.compare_digest(credentials.password, WEB_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="AgencyBill"'},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if WEB_PASSWORD:
        log.info("Web basic auth enabled for user %r", WEB_USER)
    else:
        log.warning(
            "AGENCYBILL_WEB_PASSWORD is unset — web UI is open without auth. "
            "Set it in .env before any shared deployment."
        )
    worker.ensure_alert_scanner()
    yield


app = FastAPI(title="AgencyBill", docs_url=None, redoc_url=None, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# Jinja2 filters / globals
def _currency(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"

def _short_id(v: str) -> str:
    return str(v)[:8] if v else "—"

templates.env.filters["currency"] = _currency
templates.env.filters["short_id"] = _short_id
templates.env.filters["phase_pct"] = phase_pct
templates.env.globals["now"] = lambda: datetime.now().strftime("%H:%M:%S")
templates.env.globals["PHASES"] = PHASES


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _user=Depends(_require_auth)):
    workflows = list_workflows()
    active    = [w for w in workflows if w["status"] == "active"]
    complete  = [w for w in workflows if w["status"] == "complete"]
    paused    = [w for w in workflows if w["status"] == "paused"]
    terminal  = [w for w in workflows if w["status"] in ("non_renewal","declined","cancelled")]

    queue = _pending_checkpoints()
    expiring = list_expiring_policies(90)

    return templates.TemplateResponse(request, "dashboard.html", {
        "workflows": workflows,
        "active": active,
        "complete": complete,
        "paused": paused,
        "terminal": terminal,
        "queue": queue,
        "expiring": expiring,
        "running_ids": worker.list_running(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOWS LIST
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/workflows", response_class=HTMLResponse)
async def workflows_list(request: Request, status: str = "", _user=Depends(_require_auth)):
    workflows = list_workflows(status=status or None)
    running_ids = worker.list_running()
    return templates.TemplateResponse(request, "workflows.html", {
        "workflows": workflows,
        "filter_status": status,
        "running_ids": running_ids,
    })


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW DETAIL
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/workflows/{workflow_id}", response_class=HTMLResponse)
async def workflow_detail(request: Request, workflow_id: str, _user=Depends(_require_auth)):
    wf = read_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    policy    = read_policy(wf["policy_id"])
    events    = get_audit_trail(workflow_id)
    checkpts  = get_checkpoints(workflow_id)
    invoices  = get_invoices_for_workflow(workflow_id)
    remits    = get_remittances(workflow_id)
    notifs    = get_notifications(workflow_id)
    wf_events = _workflow_events(workflow_id)
    quotes    = _get_quotes(workflow_id)
    submissions   = get_submissions(workflow_id)
    mkt_quotes    = get_market_quotes(workflow_id)
    wf_alerts     = [a for a in get_alerts_for_workflow(workflow_id)
                     if a["status"] in ("open", "acknowledged")]
    worker_status = worker.get_status(workflow_id)

    pending_cp = next((c for c in checkpts if not c.get("decision")), None)

    return templates.TemplateResponse(request, "workflow_detail.html", {
        "wf": wf,
        "policy": policy,
        "events": events,
        "checkpoints": checkpts,
        "invoices": invoices,
        "remittances": remits,
        "notifications": notifs,
        "wf_events": wf_events,
        "quotes": quotes,
        "submissions": submissions,
        "mkt_quotes": mkt_quotes,
        "wf_alerts": wf_alerts,
        "pending_cp": pending_cp,
        "worker_status": worker_status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# QUEUE  (pending HITL checkpoints)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/queue", response_class=HTMLResponse)
async def queue_view(request: Request, _user=Depends(_require_auth)):
    checkpoints = _pending_checkpoints()
    return templates.TemplateResponse(request, "queue.html", {
        "checkpoints": checkpoints,
    })


@app.post("/queue/{checkpoint_id}/decide", response_class=HTMLResponse)
async def decide_checkpoint(
    request: Request,
    checkpoint_id: str,
    decision: str = Form(...),
    notes: str = Form(""),
    _user=Depends(_require_auth),
):
    resolve_checkpoint(checkpoint_id, decision, notes=notes)
    with db() as conn:
        row = conn.execute(
            "SELECT workflow_id FROM human_checkpoints WHERE id = ?",
            (checkpoint_id,),
        ).fetchone()
    wf_id = row["workflow_id"] if row else None
    if wf_id:
        return RedirectResponse(f"/workflows/{wf_id}", status_code=303)
    return RedirectResponse("/queue", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# POLICIES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/policies", response_class=HTMLResponse)
async def policies_view(request: Request, _user=Depends(_require_auth)):
    with db() as conn:
        rows = conn.execute(
            """SELECT p.*, f.name AS firm_name, f.state AS firm_state,
                      b.name AS broker_name
               FROM policies p
               JOIN law_firms f ON p.firm_id = f.id
               LEFT JOIN brokers b ON p.broker_id = b.id
               ORDER BY p.expiration_date""",
        ).fetchall()
    policies = [row_to_dict(r) for r in rows]
    today = date.today().isoformat()
    in_90 = (date.today() + timedelta(days=90)).isoformat()

    return templates.TemplateResponse(request, "policies.html", {
        "policies": policies,
        "today": today,
        "in_90": in_90,
    })


# ─────────────────────────────────────────────────────────────────────────────
# START WORKFLOW  (POST from UI)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/workflows/start")
async def start_workflow(policy_id: str = Form(...), _user=Depends(_require_auth)):
    policy = read_policy(policy_id)
    if not policy:
        raise HTTPException(404, "Policy not found")
    wf = get_or_create_workflow(policy_id)
    worker.start_workflow(policy_id, wf["id"])
    return RedirectResponse(f"/workflows/{wf['id']}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# ALERTS & EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/alerts", response_class=HTMLResponse)
async def alerts_view(request: Request, severity: str = "", status: str = "open",
                      _user=Depends(_require_auth)):
    run_alert_scan()
    alerts = get_alerts(
        status=status or "open",
        severity=severity or None,
    )
    critical = count_open_alerts("critical")
    warning  = count_open_alerts("warning")
    info     = count_open_alerts("info")
    return templates.TemplateResponse(request, "alerts.html", {
        "alerts": alerts,
        "filter_severity": severity,
        "filter_status": status,
        "critical_count": critical,
        "warning_count": warning,
        "info_count": info,
    })


@app.post("/alerts/{alert_id}/acknowledge")
async def ack_alert(alert_id: str, _user=Depends(_require_auth)):
    acknowledge_alert(alert_id)
    return RedirectResponse("/alerts", status_code=303)


@app.post("/alerts/{alert_id}/resolve")
async def res_alert(alert_id: str, _user=Depends(_require_auth)):
    resolve_alert(alert_id)
    return RedirectResponse("/alerts", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# RECORD MARKET QUOTE
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/workflows/{workflow_id}/record-quote")
async def record_quote(
    workflow_id: str,
    submission_id: str = Form(...),
    market_id: str = Form(...),
    carrier: str = Form(""),
    quote_number: str = Form(""),
    per_claim_limit: int = Form(...),
    aggregate_limit: int = Form(...),
    deductible: int = Form(...),
    annual_premium: float = Form(...),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    retroactive_date: str = Form(""),
    notes: str = Form(""),
    _user=Depends(_require_auth),
):
    record_market_quote(
        submission_id=submission_id,
        workflow_id=workflow_id,
        market_id=market_id,
        per_claim_limit=per_claim_limit,
        aggregate_limit=aggregate_limit,
        deductible=deductible,
        annual_premium=annual_premium,
        carrier=carrier,
        quote_number=quote_number,
        effective_date=effective_date,
        expiration_date=expiration_date,
        retroactive_date=retroactive_date,
        notes=notes,
        entered_by="web_user",
    )
    return RedirectResponse(f"/workflows/{workflow_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# PARTIAL UPDATES (HTMX polling endpoints)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/htmx/workflow/{workflow_id}/status", response_class=HTMLResponse)
async def htmx_workflow_status(request: Request, workflow_id: str,
                               _user=Depends(_require_auth)):
    wf = read_workflow(workflow_id)
    worker_status = worker.get_status(workflow_id)
    pending_cp = next(
        (c for c in get_checkpoints(workflow_id) if not c.get("decision")), None
    )
    return templates.TemplateResponse(request, "partials/workflow_status.html", {
        "wf": wf,
        "worker_status": worker_status,
        "pending_cp": pending_cp,
    })


@app.get("/htmx/workflow/{workflow_id}/audit", response_class=HTMLResponse)
async def htmx_audit(request: Request, workflow_id: str, _user=Depends(_require_auth)):
    events = get_audit_trail(workflow_id)
    return templates.TemplateResponse(request, "partials/audit_rows.html", {
        "events": events,
    })


@app.get("/htmx/queue/count", response_class=HTMLResponse)
async def htmx_queue_count(request: Request, _user=Depends(_require_auth)):
    count = len(_pending_checkpoints())
    badge = f'<span id="queue-count" class="badge {"badge-error" if count else "badge-ghost"}">{count}</span>'
    return HTMLResponse(badge)


@app.get("/htmx/alerts/count", response_class=HTMLResponse)
async def htmx_alerts_count(request: Request, _user=Depends(_require_auth)):
    critical = count_open_alerts("critical")
    total    = count_open_alerts()
    cls = "badge-error" if critical else ("badge-warning" if total else "badge-ghost")
    badge = f'<span id="alerts-count" class="badge {cls}">{total}</span>'
    return HTMLResponse(badge)


@app.get("/htmx/workflow/{workflow_id}/alerts", response_class=HTMLResponse)
async def htmx_workflow_alerts(request: Request, workflow_id: str,
                               _user=Depends(_require_auth)):
    alerts = get_alerts_for_workflow(workflow_id)
    open_alerts = [a for a in alerts if a["status"] in ("open", "acknowledged")]
    return templates.TemplateResponse(request, "partials/workflow_alerts.html", {
        "alerts": open_alerts,
        "workflow_id": workflow_id,
    })


@app.get("/htmx/dashboard/stats", response_class=HTMLResponse)
async def htmx_dashboard_stats(request: Request, _user=Depends(_require_auth)):
    workflows = list_workflows()
    active   = sum(1 for w in workflows if w["status"] == "active")
    complete = sum(1 for w in workflows if w["status"] == "complete")
    queue    = len(_pending_checkpoints())
    expiring = len(list_expiring_policies(90))
    return templates.TemplateResponse(request, "partials/dashboard_stats.html", {
        "active": active,
        "complete": complete,
        "queue": queue,
        "expiring": expiring,
    })


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _pending_checkpoints() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT hc.*, w.phase AS workflow_phase,
                      p.policy_number, f.name AS firm_name
               FROM human_checkpoints hc
               JOIN renewal_workflows w ON hc.workflow_id = w.id
               JOIN policies p ON w.policy_id = p.id
               JOIN law_firms f ON p.firm_id = f.id
               WHERE hc.decision IS NULL
               ORDER BY hc.created_at""",
        ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        if d.get("options_json"):
            try:
                raw = json.loads(d["options_json"])
            except Exception as exc:
                log.warning("Bad checkpoint options_json: %s", exc)
                raw = []
            options = []
            for opt in raw:
                if isinstance(opt, dict):
                    options.append({
                        "key": opt.get("key") or opt.get("value") or opt.get("label"),
                        "label": opt.get("label") or opt.get("key") or "",
                    })
                else:
                    options.append({"key": str(opt), "label": str(opt)})
            d["options"] = options
        else:
            d["options"] = []
        result.append(d)
    return result


def _workflow_events(workflow_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY created_at",
            (workflow_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except Exception as exc:
                log.warning("Bad workflow_events data_json: %s", exc)
                d["data"] = {}
        result.append(d)
    return result


def _get_quotes(workflow_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE workflow_id = ? ORDER BY annual_premium",
            (workflow_id,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]
