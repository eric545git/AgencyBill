"""Human-in-the-loop checkpoint: pause workflow and collect human decision."""

import time

from agencybill.display.console import console, print_checkpoint
from agencybill.tools.audit_tools import create_checkpoint, resolve_checkpoint
from agencybill.database import db, row_to_dict

# Poll interval (seconds) when waiting for a web decision
_WEB_POLL_INTERVAL = 2


class HumanCheckpoint:
    """
    Pause the workflow and require a human decision before continuing.
    Supports two modes:
      - CLI mode (web_mode=False): blocks on console input
      - Web mode (web_mode=True): writes checkpoint to DB and polls until resolved
    """

    def __init__(self, workflow_id: str, web_mode: bool = False):
        self.workflow_id = workflow_id
        self.web_mode = web_mode

    def ask(self, phase: str, prompt: str, options: list[str]) -> tuple[str, str]:
        """
        Display (or register) the checkpoint and collect a decision.
        Returns (decision_key, notes).
        """
        cp_id = create_checkpoint(self.workflow_id, phase, prompt, options)

        if self.web_mode:
            return self._wait_for_web_decision(cp_id)

        return self._collect_console_decision(cp_id, prompt, options)

    def _collect_console_decision(self, cp_id: str, prompt: str,
                                   options: list[str]) -> tuple[str, str]:
        print_checkpoint(prompt, options)
        while True:
            try:
                raw = console.input(
                    "[bold yellow]Your choice (number or text): [/bold yellow]"
                ).strip()
            except (EOFError, KeyboardInterrupt):
                raw = "1"

            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    decision = options[idx]
                    break
                console.print("[red]Invalid selection. Try again.[/red]")
                continue

            matched = [o for o in options if o.lower().startswith(raw.lower())]
            if matched:
                decision = matched[0]
                break

            console.print(f"[red]Unrecognised input '{raw}'.[/red]")

        resolve_checkpoint(cp_id, decision)
        console.print(f"[bold green]✓ Decision recorded: {decision}[/bold green]")
        return decision, ""

    def _wait_for_web_decision(self, cp_id: str) -> tuple[str, str]:
        """Poll the DB until a human resolves this checkpoint via the web UI."""
        while True:
            with db() as conn:
                row = conn.execute(
                    "SELECT decision, notes FROM human_checkpoints WHERE id = ?",
                    (cp_id,),
                ).fetchone()
            if row and row["decision"]:
                return row["decision"], row["notes"] or ""
            time.sleep(_WEB_POLL_INTERVAL)
