"""Config / phase source-of-truth tests."""

from agencybill.config import (
    PHASES, AGENT_PHASES, NEXT_PHASE, PHASE_LABELS, PHASE_COLORS, phase_pct,
)
from agencybill.orchestrator import AGENT_MAP


def test_phases_include_market_submission_not_quoting():
    assert "MARKET_SUBMISSION" in PHASES
    assert "QUOTE_COLLECTION" in PHASES
    assert "QUOTING" not in PHASES
    assert "NEGOTIATION" not in PHASES


def test_agent_map_covers_agent_phases():
    assert list(AGENT_MAP.keys()) == AGENT_PHASES


def test_next_phase_chain():
    phase = "PRE_RENEWAL"
    seen = [phase]
    while phase in NEXT_PHASE:
        phase = NEXT_PHASE[phase]
        seen.append(phase)
    assert seen == PHASES


def test_phase_labels_and_colors_cover_phases():
    for p in PHASES:
        assert p in PHASE_LABELS
        assert p in PHASE_COLORS


def test_phase_pct():
    assert phase_pct("PRE_RENEWAL") == round(1 / len(PHASES) * 100)
    assert phase_pct("COMPLETE") == 100
    assert phase_pct("QUOTING") == 0
