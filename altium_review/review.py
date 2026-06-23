"""Assemble an Altium board review from eda-agent responses.

Pure given (the eda-agent responses + the distributor checker): the only network touch is
``check_fn`` (distributor stock), injected so tests stay hermetic and so the same pipeline
serves a real review (default :func:`eda_core.stock.check_stock`) and a fixture test.

The skill drives eda-agent to gather the JSON (``proj_get_bom``, ``pcb_get_design_rules``,
``pcb_get_layer_stackup``, ``pcb_get_board_info`` + measured geometry), then calls this.
"""

from __future__ import annotations

from collections.abc import Callable

from altium_review import adapter
from eda_core.jlcpcb import grade_jlcpcb
from eda_core.parts import check_parts
from eda_core.stock import check_stock


def review_board(
    *,
    bom: dict | None = None,
    rules: dict | None = None,
    stackup: dict | None = None,
    board_info: dict | None = None,
    geometry: dict | None = None,
    unit: str = "mil",
    check_fn: Callable[..., dict] = check_stock,
) -> dict:
    """Combined Altium review: BOM sourcing + JLCPCB manufacturability.

    Returns ``{sourcing: {parts, missing_mpn}, manufacturability: {...grade...}, facts: {...}}``.
    ``unit`` is the Altium length unit of the inputs ("mil"|"mm"); everything is normalised to mm.
    """
    parts = adapter.bom_to_parts(bom) if bom else []
    sourcing = check_parts(parts, check_fn) if parts else {"parts": [], "missing_mpn": []}
    facts = adapter.build_facts(
        board_info=board_info, rules=rules, stackup=stackup, geometry=geometry, unit=unit
    )
    return {
        "sourcing": sourcing,
        "manufacturability": grade_jlcpcb(facts),
        "facts": {
            "copper_layers": facts.copper_layers,
            "copper_oz": facts.copper_oz,
            "thickness_mm": facts.thickness_mm,
            "min_track_width_mm": facts.min_track_width,
            "min_via_drill_mm": facts.min_via_drill,
            "min_annular_ring_mm": facts.min_annular_ring,
            "configured_rules_mm": facts.configured_rules,
            "stackup": facts.stackup,
        },
    }
