"""FastAPI web application for AgencyBill."""

import json
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agencybill.database import init_db, db, row_to_dict
from agencybill.config import OUTPUT_DIR
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
from agencybill.web import worker

_HERE = Path(__file__).parent
app = FastAPI(title="AgencyBill", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# Jinja2 filters
def _currency(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"

def _short_id(v: str) -> str:
    return str(v)[:8] if v else "—"

def _phase_pct(phase: str) -> int:
    order = ["PRE_RENEWAL","APPLICATION","UNDERWRITING","MARKET_SUBMISSION",
             "QUOTE_COLLECTION","BINDING","INVOICING","REMITTANCE","COMPLETE"]
    try:
        return round((order.index(phase) + 1) / len(order) * 100)
    except ValueError:
        return 0

templates.env.filters["currency"] = _currency
templates.env.filters["short_id"] = _short_id
templates.env.filters["phase_pct"] = _phase_pct
templates.env.globals["now"] = lambda: datetime.now().strftime("%H:%M:%S")


@app.on_event("startup")
def _startup():
    init_db()


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
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
async def workflows_list(request: Request, status: str = ""):
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
async def workflow_detail(request: Request, workflow_id: str):
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
    submissions = get_submissions(workflow_id)
    mkt_quotes  = get_market_quotes(workflow_id)
    worker_status = worker.get_status(workflow_id)

    # pending checkpoint (if any)
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
        "pending_cp": pending_cp,
        "worker_status": worker_status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# QUEUE  (pending HITL checkpoints)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/queue", response_class=HTMLResponse)
async def queue_view(request: Request):
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
):
    resolve_checkpoint(checkpoint_id, decision, notes=notes)
    # redirect back to the workflow that owns this checkpoint
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
async def policies_view(request: Request):
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
async def start_workflow(policy_id: str = Form(...)):
    policy = read_policy(policy_id)
    if not policy:
        raise HTTPException(404, "Policy not found")
    wf = get_or_create_workflow(policy_id)
    worker.start_workflow(policy_id, wf["id"])
    return RedirectResponse(f"/workflows/{wf['id']}", status_code=303)


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
async def htmx_workflow_status(request: Request, workflow_id: str):
    """Partial: phase badge + status for HTMX polling on the detail page."""
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
async def htmx_audit(request: Request, workflow_id: str):
    """Partial: latest audit rows for HTMX polling."""
    events = get_audit_trail(workflow_id)
    return templates.TemplateResponse(request, "partials/audit_rows.html", {
        "events": events,
    })


@app.get("/htmx/queue/count", response_class=HTMLResponse)
async def htmx_queue_count(request: Request):
    count = len(_pending_checkpoints())
    badge = f'<span id="queue-count" class="badge {"badge-error" if count else "badge-ghost"}">{count}</span>'
    return HTMLResponse(badge)


@app.get("/htmx/dashboard/stats", response_class=HTMLResponse)
async def htmx_dashboard_stats(request: Request):
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
                d["options"] = json.loads(d["options_json"])
            except Exception:
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
            except Exception:
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
