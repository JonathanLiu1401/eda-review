"""The EDA-neutral intermediate representation (IR).

Backends (KiCad, Altium) read their own native formats/APIs and produce these shapes; the
shared review/grading logic consumes only the IR, so it stays backend-agnostic. Keeping the
IR tiny and explicit is what makes ``grade_jlcpcb`` and ``check_parts`` reusable across EDAs.

ALL lengths are in **millimetres**. A backend whose native units differ (Altium reports mils)
MUST convert in its adapter before building ``BoardFacts`` -- a wrong unit silently turns a
"manufacturable" verdict into garbage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class BomPart(TypedDict):
    """One placed component's sourcing identity (what the distributor check needs)."""

    ref: str
    value: str
    mpn: str
    lcsc: str


# DRC-rule keys ``grade_jlcpcb`` compares against JLCPCB minimums. A backend's adapter maps its
# own rule names onto these; KiCad already uses them verbatim in ``.kicad_pro``.
RULE_KEYS = (
    "min_copper_edge_clearance",
    "min_through_hole_diameter",
    "min_track_width",
    "min_clearance",
    "min_via_diameter",
    "min_via_annular_width",
)


@dataclass
class BoardFacts:
    """Everything ``grade_jlcpcb`` needs, in millimetres, independent of the source EDA.

    ``min_*`` are the board's *measured* worst-case geometry (the actual thinnest track, the
    actual smallest drill/ring); ``None`` means "no such primitive on the board". ``configured_rules``
    are the DRC rule floors keyed by :data:`RULE_KEYS`. ``stackup`` is a top->bottom list of
    ``{"type": "copper"|"prepreg"|"core", "thickness": mm, "epsilon_r": float|None}``.
    """

    copper_layers: int | None = None
    copper_oz: float | None = None
    thickness_mm: float | None = None
    min_track_width: float | None = None
    min_via_drill: float | None = None
    min_annular_ring: float | None = None
    configured_rules: dict = field(default_factory=dict)
    stackup: list[dict] = field(default_factory=list)
