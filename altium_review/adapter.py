"""Pure transforms: eda-agent (Altium) JSON  ->  the EDA-neutral IR.

NO IPC here -- these map the JSON that eda-agent's MCP tools return into the shapes
:mod:`eda_core` consumes. The skill drives eda-agent (its own MCP server) to get the JSON.

UNIT DISCIPLINE: Altium reports lengths in **mils** (e.g. eda-agent's DRC ``gap_mils``); the
IR is **millimetres**. Convert at the boundary with :func:`mils_to_mm`. A wrong unit silently
turns a "manufacturable" verdict into garbage -- so the per-tool ``unit`` is explicit.

CONFIDENCE: the ``proj_get_bom`` and ``pcb_run_drc`` shapes ARE documented by eda-agent and are
mapped confidently. The ``pcb_get_design_rules`` and ``pcb_get_layer_stackup`` field names were
NOT observable from eda-agent's published docs at build time -- ``rules_to_configured`` and
``stackup_to_layers`` mark their ASSUMED shapes and MUST be confirmed against a real capture on
Altium 26.7.1.11 (the Phase-0 spike) before their output is trusted.
"""

from __future__ import annotations

from eda_core.ir import BoardFacts, BomPart

MIL_TO_MM = 0.0254


def mils_to_mm(x) -> float | None:
    """mils -> mm (None-safe)."""
    return None if x is None else float(x) * MIL_TO_MM


def mm_to_mils(x) -> float | None:
    """mm -> mils (None-safe)."""
    return None if x is None else float(x) / MIL_TO_MM


def _conv(unit: str):
    """A length converter to mm for the given source unit ('mil' or 'mm')."""
    if unit == "mil":
        return mils_to_mm
    return lambda x: None if x is None else float(x)


# --------------------------------------------------------------------------- #
# BOM  (documented proj_get_bom shape -> [BomPart])
# --------------------------------------------------------------------------- #
_LCSC_KEYS = ("lcsc", "lcsc_part", "lcsc_part_number", "supplier_part")


def bom_to_parts(proj_get_bom: dict) -> list[BomPart]:
    """eda-agent ``proj_get_bom`` -> ``[{ref, value, mpn, lcsc}]`` for the sourcing sweep."""
    rows = (proj_get_bom or {}).get("bom") or []
    out: list[BomPart] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lcsc = ""
        for k in _LCSC_KEYS:
            if r.get(k):
                lcsc = str(r[k])
                break
        out.append(
            BomPart(
                ref=str(r.get("designator") or "?"),
                value=str(r.get("value") or ""),
                mpn=str(r.get("mpn") or ""),
                lcsc=lcsc,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# DRC delta  (documented pcb_run_drc shape -> new-violation list)  [edit guard]
# --------------------------------------------------------------------------- #
def _viol_key(v: dict) -> tuple:
    return (
        v.get("rule_name"),
        v.get("type"),
        v.get("layer"),
        round(float(v.get("x") or 0), 2),
        round(float(v.get("y") or 0), 2),
        v.get("net1"),
        v.get("net2"),
    )


def drc_delta(baseline: dict, after: dict) -> list[dict]:
    """Violations present in ``after`` but not in ``baseline`` -- the guard signal for an edit.

    Only *new* violations block an edit (a pre-existing violation is not the edit's fault),
    mirroring the KiCad backend's ERC/DRC-delta rule.
    """
    base = {_viol_key(v) for v in (baseline or {}).get("violations", []) if isinstance(v, dict)}
    return [
        v
        for v in (after or {}).get("violations", [])
        if isinstance(v, dict) and _viol_key(v) not in base
    ]


# --------------------------------------------------------------------------- #
# design rules  (ASSUMED shape -- CONFIRM against first real capture)
# --------------------------------------------------------------------------- #
# Altium "rule kind" -> the IR rule key grade_jlcpcb compares (None = ignored).
_RULE_KIND_MAP = {
    "clearance": "min_clearance",
    "width": "min_track_width",
    "holesize": "min_through_hole_diameter",
    "viastyle": "min_via_diameter",
    "routingviastyle": "min_via_diameter",
    "annularring": "min_via_annular_width",
    "minimumannularring": "min_via_annular_width",
}


def rules_to_configured(pcb_get_design_rules: dict, *, unit: str = "mil") -> dict:
    """Altium design rules -> ``{IR_rule_key: min_mm}`` (best effort, tightest floor per key).

    ASSUMED input shape (CONFIRM against a real capture): ``{"rules": [{"kind": str,
    "enabled": bool, "min": number}, ...]}`` with ``min`` in ``unit`` (mil|mm).
    """
    conv = _conv(unit)
    out: dict[str, float] = {}
    for r in (pcb_get_design_rules or {}).get("rules", []):
        if not isinstance(r, dict) or r.get("enabled") is False:
            continue
        key = _RULE_KIND_MAP.get(str(r.get("kind", "")).lower().replace(" ", ""))
        val = conv(r.get("min"))
        if key and val is not None:
            out[key] = min(val, out[key]) if key in out else val
    return out


# --------------------------------------------------------------------------- #
# layer stackup  (ASSUMED shape -- CONFIRM against first real capture)
# --------------------------------------------------------------------------- #
def stackup_to_layers(pcb_get_layer_stackup: dict, *, unit: str = "mil") -> list[dict]:
    """Altium layer stack -> ``[{type, thickness(mm), epsilon_r}]`` top->bottom (best effort).

    ASSUMED input shape (CONFIRM against a real capture): ``{"layers": [{"type":
    "copper"|"signal"|"plane"|"core"|"prepreg"|"dielectric", "thickness": number,
    "epsilon_r"|"dielectric_constant": number}, ...]}``.
    """
    conv = _conv(unit)
    out: list[dict] = []
    for layer in (pcb_get_layer_stackup or {}).get("layers", []):
        if not isinstance(layer, dict):
            continue
        t = str(layer.get("type", "")).lower()
        th = conv(layer.get("thickness"))
        er = layer.get("epsilon_r") or layer.get("dielectric_constant")
        if "copper" in t or "signal" in t or "plane" in t:
            out.append({"type": "copper", "thickness": th})
        elif "core" in t:
            out.append({"type": "core", "thickness": th, "epsilon_r": er})
        elif "prepreg" in t or "dielectric" in t:
            out.append({"type": "prepreg", "thickness": th, "epsilon_r": er})
    return out


# --------------------------------------------------------------------------- #
# facts assembly
# --------------------------------------------------------------------------- #
def build_facts(
    *,
    board_info: dict | None = None,
    rules: dict | None = None,
    stackup: dict | None = None,
    geometry: dict | None = None,
    unit: str = "mil",
) -> BoardFacts:
    """Assemble :class:`BoardFacts` (mm) from eda-agent responses.

    ``board_info`` carries ``copper_layers``/``layer_count``, ``copper_oz``, ``thickness`` (in
    ``unit``). ``geometry`` carries the measured worst-case minimums ``min_track_width`` /
    ``min_via_drill`` / ``min_annular_ring`` (in ``unit``) the skill computes from PCB-primitive
    queries. ``rules`` / ``stackup`` are the raw rule / stackup tool responses.
    """
    bi = board_info or {}
    geo = geometry or {}
    conv = _conv(unit)
    return BoardFacts(
        copper_layers=bi.get("copper_layers") or bi.get("layer_count"),
        copper_oz=bi.get("copper_oz"),
        thickness_mm=conv(bi.get("thickness")),
        min_track_width=conv(geo.get("min_track_width")),
        min_via_drill=conv(geo.get("min_via_drill")),
        min_annular_ring=conv(geo.get("min_annular_ring")),
        configured_rules=rules_to_configured(rules, unit=unit) if rules else {},
        stackup=stackup_to_layers(stackup, unit=unit) if stackup else [],
    )
