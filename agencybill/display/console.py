"""Rich console helpers for AgencyBill CLI display."""

from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()


PHASE_COLORS = {
    "PRE_RENEWAL":  "cyan",
    "APPLICATION":  "blue",
    "UNDERWRITING": "yellow",
    "QUOTING":      "magenta",
    "NEGOTIATION":  "bright_yellow",
    "BINDING":      "green",
    "INVOICING":    "bright_cyan",
    "REMITTANCE":   "bright_green",
    "COMPLETE":     "bold green",
    "NON_RENEWAL":  "red",
    "DECLINED":     "bold red",
}

STATUS_COLORS = {
    "active":      "green",
    "paused":      "yellow",
    "complete":    "bold green",
    "non_renewal": "red",
    "declined":    "bold red",
    "cancelled":   "dim",
}


def print_header(title: str) -> None:
    console.print(Panel(f"[bold white]{title}[/bold white]",
                        style="blue", box=box.DOUBLE))


def print_phase_banner(phase: str, workflow_id: str) -> None:
    label = {
        "PRE_RENEWAL":  "Phase 1 — Pre-Renewal Triage",
        "APPLICATION":  "Phase 2 — Renewal Application",
        "UNDERWRITING": "Phase 3 — Underwriting Review",
        "QUOTING":      "Phase 4 — Quote Generation",
        "NEGOTIATION":  "Phase 5 — Quote Negotiation",
        "BINDING":      "Phase 6 — Binding & Issuance",
        "INVOICING":    "Phase 7 — Invoicing & Payment",
        "REMITTANCE":   "Phase 8 — Premium Remittance",
        "COMPLETE":     "Workflow Complete",
        "NON_RENEWAL":  "Non-Renewal",
        "DECLINED":     "Declined",
    }.get(phase, phase)
    color = PHASE_COLORS.get(phase, "white")
    console.print()
    console.print(Panel(
        f"[bold {color}]{label}[/bold {color}]\n"
        f"[dim]Workflow: {workflow_id}[/dim]",
        style=color,
        box=box.ROUNDED,
    ))


def print_agent_action(agent_name: str, action: str, detail: str = "") -> None:
    color = "cyan"
    ts = datetime.now().strftime("%H:%M:%S")
    msg = f"[dim]{ts}[/dim] [bold {color}][{agent_name}][/bold {color}] {action}"
    if detail:
        msg += f"\n         [dim]{detail}[/dim]"
    console.print(msg)


def print_tool_call(tool_name: str, result_summary: str = "") -> None:
    console.print(f"  [dim]→ tool:[/dim] [yellow]{tool_name}[/yellow]"
                  + (f"  [dim]{result_summary}[/dim]" if result_summary else ""))


def print_agent_message(agent_name: str, message: str) -> None:
    console.print(Panel(
        message,
        title=f"[bold cyan]{agent_name}[/bold cyan]",
        border_style="cyan",
        box=box.SIMPLE,
    ))


def print_checkpoint(prompt: str, options: list[str]) -> None:
    console.print()
    console.print(Panel(
        f"[bold yellow]{prompt}[/bold yellow]",
        title="[bold]Human Checkpoint Required[/bold]",
        border_style="yellow",
        box=box.HEAVY,
    ))
    for i, opt in enumerate(options, 1):
        console.print(f"  [bold]{i}.[/bold] {opt}")
    console.print()


def print_workflows_table(workflows: list[dict]) -> None:
    if not workflows:
        console.print("[dim]No workflows found.[/dim]")
        return
    t = Table(title="Renewal Workflows", box=box.ROUNDED, show_lines=False)
    t.add_column("Workflow ID", style="dim", width=12)
    t.add_column("Firm", style="white")
    t.add_column("Policy #", style="cyan")
    t.add_column("Expiration", style="yellow")
    t.add_column("Phase", style="magenta")
    t.add_column("Status", style="green")
    t.add_column("Updated", style="dim")
    for w in workflows:
        wid = w.get("id", "")[:8]
        color = PHASE_COLORS.get(w.get("phase", ""), "white")
        sc = STATUS_COLORS.get(w.get("status", ""), "white")
        t.add_row(
            wid,
            w.get("firm_name", "—"),
            w.get("policy_number", "—"),
            w.get("expiration_date", "—"),
            f"[{color}]{w.get('phase', '—')}[/{color}]",
            f"[{sc}]{w.get('status', '—')}[/{sc}]",
            (w.get("updated_at") or "")[:16],
        )
    console.print(t)


def print_audit_table(events: list[dict]) -> None:
    if not events:
        console.print("[dim]No audit events.[/dim]")
        return
    t = Table(title="Audit Trail", box=box.SIMPLE_HEAD, show_lines=True)
    t.add_column("Time", style="dim", width=19)
    t.add_column("Agent", style="cyan", width=20)
    t.add_column("Action", style="white")
    t.add_column("Details", style="dim")
    for e in events:
        details = e.get("details", {})
        detail_str = ", ".join(f"{k}={v}" for k, v in list(details.items())[:3]) if details else ""
        t.add_row(
            (e.get("created_at") or "")[:19],
            e.get("agent_name", "—"),
            e.get("action", "—"),
            detail_str,
        )
    console.print(t)


def print_policies_table(policies: list[dict]) -> None:
    if not policies:
        console.print("[dim]No expiring policies found.[/dim]")
        return
    t = Table(title="Expiring Policies", box=box.ROUNDED)
    t.add_column("Policy ID", style="dim", width=12)
    t.add_column("Firm", style="white")
    t.add_column("Policy #", style="cyan")
    t.add_column("Expiration", style="yellow")
    t.add_column("Premium", style="green", justify="right")
    t.add_column("State", style="blue", width=6)
    for p in policies:
        t.add_row(
            p.get("id", "")[:8],
            p.get("firm_name", "—"),
            p.get("policy_number", "—"),
            p.get("expiration_date", "—"),
            f"${p.get('annual_premium', 0):,.2f}",
            p.get("firm_state", "—"),
        )
    console.print(t)


def print_quote_table(quotes: list[dict]) -> None:
    if not quotes:
        console.print("[dim]No quotes generated.[/dim]")
        return
    t = Table(title="Quote Options", box=box.ROUNDED)
    t.add_column("Option", style="bold")
    t.add_column("Limit (per/agg)", style="cyan")
    t.add_column("Deductible", style="yellow", justify="right")
    t.add_column("Annual Premium", style="bold green", justify="right")
    t.add_column("Status", style="magenta")
    for q in quotes:
        lim = f"${q.get('per_claim_limit',0)//1000:,}K / ${q.get('aggregate_limit',0)//1000:,}K"
        t.add_row(
            q.get("option_label", "—"),
            lim,
            f"${q.get('deductible', 0):,}",
            f"${q.get('annual_premium', 0):,.2f}",
            q.get("status", "—"),
        )
    console.print(t)


def print_success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def print_error(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


def print_warning(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[bold blue]ℹ[/bold blue] {msg}")
