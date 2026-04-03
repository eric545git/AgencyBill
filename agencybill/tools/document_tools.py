"""Tools for rendering Jinja2 templates and saving generated documents."""

import uuid
from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from agencybill.config import TEMPLATES_DIR, OUTPUT_DIR


_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        _env.filters["currency"] = lambda v: f"${v:,.2f}" if v is not None else "$0.00"
        _env.filters["dateformat"] = lambda v: v[:10] if v else ""
    return _env


def render_template(template_name: str, context: dict) -> str:
    """Render a Jinja2 template with the given context and return HTML string."""
    env = _get_env()
    template = env.get_template(template_name)
    context.setdefault("generated_at", datetime.now().strftime("%B %d, %Y %H:%M"))
    return template.render(**context)


def save_document(template_name: str, context: dict, filename_prefix: str = "") -> str:
    """Render template and save to output/. Returns the saved file path."""
    html = render_template(template_name, context)
    slug = template_name.replace(".html", "")
    prefix = f"{filename_prefix}_" if filename_prefix else ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}{slug}_{ts}.html"
    out_path = OUTPUT_DIR / filename
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


def list_documents(prefix: str = "") -> list[str]:
    """List all generated documents in the output directory."""
    pattern = f"{prefix}*" if prefix else "*.html"
    return sorted(str(p) for p in OUTPUT_DIR.glob(pattern))
