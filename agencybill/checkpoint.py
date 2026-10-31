"""Human-in-the-loop checkpoint: pause workflow and collect human decision."""

import time
from typing import Any

from agencybill.display.console import console, print_checkpoint
from agencybill.tools.audit_tools import create_checkpoint, resolve_checkpoint
from agencybill.database import db
from agencybill.logging_config import get_logger

log = get_logger("agencybill.checkpoint")

# Poll interval (seconds) when waiting for a web decision
_WEB_POLL_INTERVAL = 2


def normalize_options(options: list[Any]) -> list[dict]:
    """Normalize options to [{key, label}, ...]. Strings become key=label."""
    normalized = []
    for opt in options:
        if isinstance(opt, dict):
            key = opt.get("key") or opt.get("value") or opt.get("label")
            label = opt.get("label") or opt.get("key") or str(key)
            normalized.append({"key": str(key), "label": str(label)})
        else:
            normalized.append({"key": str(opt), "label": str(opt)})
    return normalized


class HumanCheckpoint:
    """
    Pause the workflow and require a human decision before continuing.
    Supports two modes:
      - CLI mode (web_mode=False): blocks on console input
      - Web mode (web_mode=True): writes checkpoint to DB and polls until resolved

    Returns (decision_key, notes). Decision keys are stable identifiers
    (confirm / hold / decline / accept / refer / awaiting / cancel), not labels.
    """

    def __init__(self, workflow_id: str, web_mode: bool = False):
        self.workflow_id = workflow_id
        self.web_mode = web_mode

    def ask(self, phase: str, prompt: str, options: list[Any]) -> tuple[str, str]:
        """
        Display (or register) the checkpoint and collect a decision.
        Returns (decision_key, notes).
        """
        opts = normalize_options(options)
        cp_id = create_checkpoint(self.workflow_id, phase, prompt, opts)

        if self.web_mode:
            return self._wait_for_web_decision(cp_id)

        return self._collect_console_decision(cp_id, prompt, opts)

    def _collect_console_decision(self, cp_id: str, prompt: str,
                                   options: list[dict]) -> tuple[str, str]:
        labels = [o["label"] for o in options]
        print_checkpoint(prompt, labels)
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
                    decision = options[idx]["key"]
                    break
                console.print("[red]Invalid selection. Try again.[/red]")
                continue

            # Match by key or label prefix
            matched = [
                o for o in options
                if o["key"].lower() == raw.lower()
                or o["label"].lower().startswith(raw.lower())
            ]
            if matched:
                decision = matched[0]["key"]
                break

            console.print(f"[red]Unrecognised input '{raw}'.[/red]")

        resolve_checkpoint(cp_id, decision)
        label = next((o["label"] for o in options if o["key"] == decision), decision)
        console.print(f"[bold green]✓ Decision recorded: {label}[/bold green]")
        return decision, ""

    def _wait_for_web_decision(self, cp_id: str) -> tuple[str, str]:
        """Poll the DB until a human resolves this checkpoint via the web UI."""
        log.info("Waiting for web decision on checkpoint %s", cp_id)
        while True:
            with db() as conn:
                row = conn.execute(
                    "SELECT decision, notes FROM human_checkpoints WHERE id = ?",
                    (cp_id,),
                ).fetchone()
            if row and row["decision"]:
                return row["decision"], row["notes"] or ""
            time.sleep(_WEB_POLL_INTERVAL)
