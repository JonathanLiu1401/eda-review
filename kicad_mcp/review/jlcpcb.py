"""JLCPCB manufacturability for a KiCad board: read the board's facts, then grade them.

The authoritative capability data and the grading logic live in :mod:`eda_core.jlcpcb`
(EDA-neutral). This module does only the KiCad-specific *reading* -- ``.kicad_pcb`` geometry
and stackup, ``.kicad_pro`` configured rules -- and assembles the :class:`eda_core.ir.BoardFacts`
the grader consumes. Read-only. The capability values are cited (NOT invented); see
``eda_core.jlcpcb.SOURCES``/``VERIFIED``.
"""

from __future__ import annotations

import json
from pathlib import Path

import sexpdata

from eda_core.ir import BoardFacts

# Re-exported so existing callers/tests keep using ``kicad_mcp.review.jlcpcb.<name>``.
from eda_core.jlcpcb import (  # noqa: F401
    SOURCES,
    STACKUP_SOURCE,
    STACKUPS,
    SUPPORTED_COPPER_OZ,
    SUPPORTED_THICKNESS_MM,
    VERIFIED,
    _stackup_dielectric_mismatch,
    capabilities_for,
    grade_jlcpcb,
    reference_stackup,
)
from kicad_mcp.review.parse import _get, _getall, _getval, parse_board


# --------------------------------------------------------------------------- #
# KiCad-format readers (produce the EDA-neutral facts grade_jlcpcb needs)
# --------------------------------------------------------------------------- #
def parse_board_stackup(pcb_path: str | Path) -> list[dict]:
    """The board's dielectric+copper stackup layers top->bottom, or [] when none is defined."""
    try:
        data = sexpdata.loads(Path(pcb_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    setup = _get(data, "setup")
    stk = _get(setup, "stackup") if setup else None
    if not stk:
        return []
    out: list[dict] = []
    for node in _getall(stk, "layer"):
        typ = str(_getval(node, "type") or "").lower()
        th = _getval(node, "thickness")
        if "copper" in typ:
            out.append({"type": "copper", "thickness": th})
        elif "prepreg" in typ or "core" in typ:
            out.append(
                {
                    "type": "core" if "core" in typ else "prepreg",
                    "thickness": th,
                    "epsilon_r": _getval(node, "epsilon_r"),
                }
            )
    return out


def board_rules(pro_path: str | Path | None) -> dict:
    """The configured ``design_settings.rules`` from a ``.kicad_pro`` (or {})."""
    if not pro_path:
        return {}
    try:
        d = json.loads(Path(pro_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (d.get("board") or {}).get("design_settings", {}).get("rules", {}) or {}


def board_thickness_mm(pcb_path: str | Path) -> float | None:
    """The finished board thickness from ``(general (thickness X))`` in the .kicad_pcb."""
    try:
        data = sexpdata.loads(Path(pcb_path).read_text(encoding="utf-8"))
        gen = _get(data, "general")
        t = _getval(gen, "thickness") if gen else None
        return float(t) if t is not None else None
    except (OSError, ValueError, TypeError):
        return None


def board_facts(project) -> BoardFacts:
    """Assemble the EDA-neutral :class:`BoardFacts` from a KiCad project (mm throughout)."""
    board = parse_board(project.pcb)
    widths = [t.width for t in board.tracks if t.width > 0]
    drills = [v.drill for v in board.vias if v.drill > 0]
    rings = [(v.size - v.drill) / 2 for v in board.vias if v.size > 0 and v.drill > 0]
    return BoardFacts(
        copper_layers=board.copper_layers,
        copper_oz=board.copper_oz,
        thickness_mm=board_thickness_mm(project.pcb),
        min_track_width=min(widths) if widths else None,
        min_via_drill=min(drills) if drills else None,
        min_annular_ring=min(rings) if rings else None,
        configured_rules=board_rules(project.pro),
        stackup=parse_board_stackup(project.pcb),
    )


# --------------------------------------------------------------------------- #
# the check (KiCad board -> facts -> shared grade)
# --------------------------------------------------------------------------- #
def check_jlcpcb_manufacturability(project) -> dict:
    """Grade a KiCad board against JLCPCB's published capabilities.

    Returns ``{manufacturable, layers, copper_oz, thickness_mm, limits, reference_stackup,
    findings, sources, verified}``. ``manufacturable`` is False on any blocker (a real feature
    JLCPCB cannot make). Looser-than-JLCPCB *configured rules* are 'major' warnings.
    """
    if not project.pcb:
        from kicad_mcp.review.kicad import KiCadError

        raise KiCadError("No PCB to check against JLCPCB.")
    return grade_jlcpcb(board_facts(project))
