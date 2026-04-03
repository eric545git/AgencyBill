"""Human-in-the-loop checkpoint: pause workflow and collect human decision."""

from agencybill.display.console import console, print_checkpoint
from agencybill.tools.audit_tools import create_checkpoint, resolve_checkpoint


class HumanCheckpoint:
    """Pause the workflow and require a human decision before continuing."""

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id

    def ask(self, phase: str, prompt: str, options: list[str]) -> tuple[str, str]:
        """
        Display the checkpoint, collect input, record the decision.
        Returns (decision_key, notes).
        """
        cp_id = create_checkpoint(self.workflow_id, phase, prompt, options)
        print_checkpoint(prompt, options)

        while True:
            try:
                raw = console.input("[bold yellow]Your choice (number or text, or 'n' for notes): [/bold yellow]").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "1"  # default to first option in non-interactive mode

            # Numeric selection
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    decision = options[idx]
                    notes = ""
                    break
                else:
                    console.print("[red]Invalid selection. Try again.[/red]")
                    continue

            # Text match
            matched = [o for o in options if o.lower().startswith(raw.lower())]
            if matched:
                decision = matched[0]
                notes = ""
                break

            console.print(f"[red]Unrecognised input '{raw}'. Enter a number or option text.[/red]")

        resolve_checkpoint(cp_id, decision, notes=notes)
        console.print(f"[bold green]✓ Decision recorded: {decision}[/bold green]")
        return decision, notes
