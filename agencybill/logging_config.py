"""Shared logging setup for AgencyBill."""

import logging
import sys

_configured = False


def get_logger(name: str = "agencybill") -> logging.Logger:
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root = logging.getLogger("agencybill")
        root.setLevel(logging.INFO)
        if not root.handlers:
            root.addHandler(handler)
        _configured = True
    return logging.getLogger(name)
