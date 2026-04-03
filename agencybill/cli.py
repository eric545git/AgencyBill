"""Click CLI for AgencyBill."""

import sys
import json
from pathlib import Path

import click
from rich.console import Console

from agencybill.database import init_db
from agencybill.display.console import (
    console, print_header, print_workflows_table, print_audit_table,
    print_policies_table, print_success, print_error, print_info,
)

console_err = Console(stderr=True)


@click.group()
def cli():
    """AgencyBill — Agentic LPL Agency Bill Renewal Workflow CLI."""
    init_db()


# ─────────────────────────────────────────────
# seed
# ─────────────────────────────────────────────

@cli.command()
def seed():
    """Load sample firms, attorneys, brokers, policies, and claims into the database."""
    from agencybill.config import FIXTURES_DIR
    from agencybill.database import db
    import json

    print_header("Seeding Sample Data")

    def load(filename: str) -> list:
        p = FIXTURES_DIR / filename
        if not p.exists():
            console.print(f"[yellow]Fixture not found: {filename}[/yellow]")
            return []
        return json.loads(p.read_text())

    brokers = load("seed_brokers.json")
    firms = load("seed_firms.json")
    attorneys = load("seed_attorneys.json")
    policies = load("seed_policies.json")
    claims = load("seed_claims.json")

    with db() as conn:
        # Brokers
        for b in brokers:
            conn.execute(
                """INSERT OR IGNORE INTO brokers (id, name, license_number, state, email, phone, agency_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (b["id"], b["name"], b.get("license_number"), b.get("state"),
                 b.get("email"), b.get("phone"), b.get("agency_name")),
            )
        # Firms
        for f in firms:
            conn.execute(
                """INSERT OR IGNORE INTO law_firms
                   (id, name, tax_id, address_line1, city, state, zip, phone, email, firm_type, year_founded)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["id"], f["name"], f.get("tax_id"), f.get("address_line1"),
                 f.get("city"), f["state"], f.get("zip"), f.get("phone"),
                 f.get("email"), f.get("firm_type"), f.get("year_founded")),
            )
        # Attorneys
        for a in attorneys:
            pa = a.get("practice_areas", [])
            conn.execute(
                """INSERT OR IGNORE INTO attorneys
                   (id, firm_id, name, bar_number, state_licensed, practice_areas, years_admitted)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (a["id"], a["firm_id"], a["name"], a.get("bar_number"),
                 a.get("state_licensed"),
                 json.dumps(pa) if isinstance(pa, list) else pa,
                 a.get("years_admitted")),
            )
        # Policies
        for p in policies:
            conn.execute(
                """INSERT OR IGNORE INTO policies
                   (id, firm_id, broker_id, policy_number, carrier,
                    effective_date, expiration_date, limit_per_claim,
                    aggregate_limit, deductible, annual_premium, status, attorney_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], p["firm_id"], p.get("broker_id"), p["policy_number"],
                 p["carrier"], p["effective_date"], p["expiration_date"],
                 p["limit_per_claim"], p["aggregate_limit"], p["deductible"],
                 p["annual_premium"], p.get("status", "active"), p.get("attorney_count", 1)),
            )
        # Claims
        for c in claims:
            conn.execute(
                """INSERT OR IGNORE INTO claims
                   (id, policy_id, firm_id, claim_number, claim_date, closed_date,
                    claim_type, practice_area, amount_paid, amount_reserved, status, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (c["id"], c["policy_id"], c["firm_id"], c.get("claim_number"),
                 c["claim_date"], c.get("closed_date"), c.get("claim_type"),
                 c.get("practice_area"), c.get("amount_paid", 0),
                 c.get("amount_reserved", 0), c.get("status", "open"),
                 c.get("description")),
            )

    print_success(f"Seeded {len(brokers)} brokers, {len(firms)} firms, "
                  f"{len(attorneys)} attorneys, {len(policies)} policies, "
                  f"{len(claims)} claims.")


# ─────────────────────────────────────────────
# list-expiring
# ─────────────────────────────────────────────

@cli.command("list-expiring")
@click.option("--days", default=90, show_default=True,
              help="Number of days ahead to look for expiring policies")
def list_expiring(days: int):
    """List all active policies expiring within N days."""
    from agencybill.tools.policy_tools import list_expiring_policies
    print_header(f"Policies Expiring in the Next {days} Days")
    policies = list_expiring_policies(days)
    print_policies_table(policies)
    print_info(f"{len(policies)} policy(ies) found.")


# ─────────────────────────────────────────────
# start
# ─────────────────────────────────────────────

@cli.command()
@click.option("--policy-id", required=True, help="Policy ID to start a renewal workflow for")
@click.option("--auto", is_flag=True, default=False,
              help="Auto mode: skip human-in-the-loop checkpoints")
def start(policy_id: str, auto: bool):
    """Start a new renewal workflow for a given policy."""
    from agencybill.tools.policy_tools import read_policy, get_or_create_workflow
    from agencybill.orchestrator import WorkflowOrchestrator

    print_header("Starting Renewal Workflow")

    policy = read_policy(policy_id)
    if not policy:
        print_error(f"Policy not found: {policy_id}")
        sys.exit(1)

    print_info(f"Policy: {policy['policy_number']} | Firm: {policy['firm_name']} | "
               f"Expires: {policy['expiration_date']}")

    workflow = get_or_create_workflow(policy_id)
    workflow_id = workflow["id"]
    print_success(f"Workflow ID: {workflow_id}")

    orchestrator = WorkflowOrchestrator(workflow_id, policy_id, auto_mode=auto)
    orchestrator.run()


# ─────────────────────────────────────────────
# resume
# ─────────────────────────────────────────────

@cli.command()
@click.option("--workflow-id", required=True, help="Workflow ID to resume")
@click.option("--auto", is_flag=True, default=False, help="Auto mode: skip HITL checkpoints")
def resume(workflow_id: str, auto: bool):
    """Resume a paused or in-progress workflow."""
    from agencybill.tools.policy_tools import read_workflow
    from agencybill.orchestrator import WorkflowOrchestrator

    print_header("Resuming Renewal Workflow")

    workflow = read_workflow(workflow_id)
    if not workflow:
        print_error(f"Workflow not found: {workflow_id}")
        sys.exit(1)

    print_info(f"Workflow: {workflow_id} | Phase: {workflow['phase']} | Status: {workflow['status']}")

    orchestrator = WorkflowOrchestrator(workflow_id, workflow["policy_id"], auto_mode=auto)
    orchestrator.run()


# ─────────────────────────────────────────────
# status
# ─────────────────────────────────────────────

@cli.command()
@click.option("--workflow-id", default=None, help="Specific workflow ID (optional)")
@click.option("--status-filter", default=None, help="Filter by status (active/paused/complete/etc)")
def status(workflow_id: str, status_filter: str):
    """Show status of all workflows or a specific one."""
    from agencybill.tools.policy_tools import read_workflow, list_workflows
    from agencybill.tools.payment_tools import get_invoices_for_workflow, get_remittances

    print_header("Renewal Workflow Status")

    if workflow_id:
        wf = read_workflow(workflow_id)
        if not wf:
            print_error(f"Workflow not found: {workflow_id}")
            sys.exit(1)
        print_workflows_table([wf])
        # Show invoice/payment summary
        invoices = get_invoices_for_workflow(workflow_id)
        if invoices:
            console.print()
            console.print("[bold]Invoices:[/bold]")
            for inv in invoices:
                console.print(f"  {inv['invoice_number']} — "
                               f"${inv['amount_due']:,.2f} — {inv['status']}")
        remittances = get_remittances(workflow_id)
        if remittances:
            console.print()
            console.print("[bold]Remittances:[/bold]")
            for rem in remittances:
                console.print(f"  {rem['reference_number']} — "
                               f"${rem['amount']:,.2f} to {rem['carrier']} — {rem['status']}")
    else:
        workflows = list_workflows(status=status_filter)
        print_workflows_table(workflows)
        print_info(f"{len(workflows)} workflow(s) found.")


# ─────────────────────────────────────────────
# audit
# ─────────────────────────────────────────────

@cli.command()
@click.option("--workflow-id", required=True, help="Workflow ID to show audit trail for")
def audit(workflow_id: str):
    """Print the full audit trail for a workflow."""
    from agencybill.tools.audit_tools import get_audit_trail, get_checkpoints

    print_header(f"Audit Trail — {workflow_id}")
    events = get_audit_trail(workflow_id)
    print_audit_table(events)
    print_info(f"{len(events)} audit event(s).")

    checkpoints = get_checkpoints(workflow_id)
    if checkpoints:
        console.print()
        console.print("[bold]Human Checkpoints:[/bold]")
        for cp in checkpoints:
            decided = cp.get("decided_at", "—")[:16] if cp.get("decided_at") else "pending"
            console.print(f"  [{cp['phase']}] {cp['prompt'][:60]} → "
                           f"[bold]{cp.get('decision', 'pending')}[/bold] ({decided})")


# ─────────────────────────────────────────────
# policies
# ─────────────────────────────────────────────

@cli.command()
def policies():
    """List all policies in the database."""
    from agencybill.database import db, row_to_dict
    print_header("All Policies")
    with db() as conn:
        rows = conn.execute(
            """SELECT p.*, f.name AS firm_name, f.state AS firm_state
               FROM policies p JOIN law_firms f ON p.firm_id = f.id
               ORDER BY p.expiration_date""",
        ).fetchall()
    data = [row_to_dict(r) for r in rows]
    print_policies_table(data)
    print_info(f"{len(data)} policy(ies).")


if __name__ == "__main__":
    cli()
