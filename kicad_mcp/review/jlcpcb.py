"""JLCPCB manufacturability: check a board against JLCPCB's *authoritative* capabilities.

The capability values below are transcribed and cross-referenced from JLCPCB's own published
pages (NOT invented) -- see ``SOURCES``/``VERIFIED``. Re-verify when JLCPCB updates. The check
compares (1) the board's ACTUAL geometry and (2) its CONFIGURED design rules against these
limits, so the review is JLCPCB-meaningful rather than KiCad-default. Read-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import sexpdata

from kicad_mcp.review.parse import _get, _getval, parse_board

# --------------------------------------------------------------------------- #
# authoritative capability data (cited, not hallucinated)
# --------------------------------------------------------------------------- #
SOURCES = (
    "https://jlcpcb.com/capabilities/pcb-capabilities",
    "https://www.schemalyzer.com/en/blog/manufacturing/jlcpcb/jlcpcb-design-rules",
)
VERIFIED = "2026-06-17"  # re-confirm against SOURCES after this date

# all values in mm. JLCPCB's minimums tighten on 4+ layer boards.
_LIMITS_1_2_LAYER = {
    "min_track_width": 0.127,  # 5 mil (1 oz outer)
    "min_clearance": 0.127,
    "min_via_drill": 0.30,
    "min_via_diameter": 0.60,
    "min_annular_ring": 0.15,
    "min_pth": 0.15,
    "min_copper_edge_clearance": 0.30,
}
_LIMITS_4PLUS_LAYER = {
    "min_track_width": 0.09,  # 3.5 mil
    "min_clearance": 0.09,
    "min_via_drill": 0.20,
    "min_via_diameter": 0.45,
    "min_annular_ring": 0.15,
    "min_pth": 0.15,
    "min_copper_edge_clearance": 0.30,
}
SUPPORTED_COPPER_OZ = (0.5, 1.0, 2.0)
SUPPORTED_THICKNESS_MM = (0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0)


def capabilities_for(copper_layers: int | None, copper_oz: float | None) -> dict:
    """JLCPCB min-feature limits (mm) for a board of this layer count + copper weight."""
    limits = dict(_LIMITS_4PLUS_LAYER if (copper_layers or 2) >= 4 else _LIMITS_1_2_LAYER)
    if (copper_oz or 1) >= 2:  # 2 oz copper needs wider min track/clearance
        limits["min_track_width"] = max(limits["min_track_width"], 0.20)
        limits["min_clearance"] = max(limits["min_clearance"], 0.20)
    return limits


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
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


def _f(severity: str, title: str, detail: str) -> dict:
    return {"severity": severity, "title": title, "detail": detail}


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #
def check_jlcpcb_manufacturability(project) -> dict:
    """Grade a board against JLCPCB's published capabilities.

    Returns ``{manufacturable, layers, copper_oz, thickness_mm, limits, findings, sources,
    verified}``. ``manufacturable`` is False on any blocker (a real feature JLCPCB cannot make).
    Looser-than-JLCPCB *configured rules* are 'major' warnings (the fab is fine, but KiCad's DRC
    won't catch a sub-JLCPCB feature you add later).
    """
    if not project.pcb:
        from kicad_mcp.review.kicad import KiCadError

        raise KiCadError("No PCB to check against JLCPCB.")

    board = parse_board(project.pcb)
    limits = capabilities_for(board.copper_layers, board.copper_oz)
    findings: list[dict] = []

    # 1) actual geometry JLCPCB physically cannot make -> blockers
    widths = [t.width for t in board.tracks if t.width > 0]
    if widths and min(widths) < limits["min_track_width"] - 1e-6:
        findings.append(
            _f(
                "blocker",
                f"Thinnest track {min(widths):.3f} mm < JLCPCB min {limits['min_track_width']} mm",
                f"JLCPCB cannot reliably etch tracks below {limits['min_track_width']} mm on a "
                f"{board.copper_layers}-layer / {board.copper_oz:.0f} oz board. Widen them.",
            )
        )
    drills = [v.drill for v in board.vias if v.drill > 0]
    if drills and min(drills) < limits["min_via_drill"] - 1e-6:
        findings.append(
            _f(
                "blocker",
                f"Smallest via drill {min(drills):.3f} mm < JLCPCB min {limits['min_via_drill']} mm",
                "JLCPCB mechanical drilling cannot make a via hole this small. Enlarge the drill.",
            )
        )
    rings = [(v.size - v.drill) / 2 for v in board.vias if v.size > 0 and v.drill > 0]
    if rings and min(rings) < limits["min_annular_ring"] - 1e-6:
        findings.append(
            _f(
                "blocker",
                f"Smallest via annular ring {min(rings):.3f} mm < JLCPCB min "
                f"{limits['min_annular_ring']} mm",
                "Annular ring = (via diameter - drill) / 2. Increase the via pad or shrink the drill.",
            )
        )

    # 2) configured DRC rules looser than JLCPCB -> KiCad DRC won't catch sub-JLCPCB features
    rules = board_rules(project.pro)
    for key, limit_key, label in (
        ("min_copper_edge_clearance", "min_copper_edge_clearance", "copper-to-board-edge"),
        ("min_through_hole_diameter", "min_pth", "min plated hole"),
        ("min_track_width", "min_track_width", "min track width"),
        ("min_clearance", "min_clearance", "min clearance"),
        ("min_via_diameter", "min_via_diameter", "min via diameter"),
        ("min_via_annular_width", "min_annular_ring", "min annular ring"),
    ):
        cur = rules.get(key)
        jlc = limits[limit_key]
        if isinstance(cur, int | float) and cur < jlc - 1e-6:
            findings.append(
                _f(
                    "major",
                    f"DRC rule '{label}' = {cur} mm is looser than JLCPCB's {jlc} mm",
                    "KiCad's DRC will pass features JLCPCB can't make. Tighten this rule to the "
                    "JLCPCB minimum (or run `jlcpcb-apply-rules`).",
                )
            )

    # 3) stackup / layer-count / copper / thickness must be a JLCPCB-orderable combo
    if board.copper_oz and round(board.copper_oz, 1) not in SUPPORTED_COPPER_OZ:
        findings.append(
            _f(
                "major",
                f"Copper weight {board.copper_oz:.1f} oz is not a standard JLCPCB option",
                f"JLCPCB standard copper weights: {', '.join(f'{o:g}' for o in SUPPORTED_COPPER_OZ)} oz.",
            )
        )
    thickness = board_thickness_mm(project.pcb)
    if thickness and round(thickness, 2) not in SUPPORTED_THICKNESS_MM:
        findings.append(
            _f(
                "major",
                f"Board thickness {thickness} mm is not a standard JLCPCB option",
                f"JLCPCB standard thicknesses (mm): "
                f"{', '.join(f'{t:g}' for t in SUPPORTED_THICKNESS_MM)}.",
            )
        )

    manufacturable = not any(f["severity"] == "blocker" for f in findings)
    return {
        "manufacturable": manufacturable,
        "layers": board.copper_layers,
        "copper_oz": board.copper_oz,
        "thickness_mm": thickness,
        "limits": limits,
        "findings": findings,
        "sources": list(SOURCES),
        "verified": VERIFIED,
    }
