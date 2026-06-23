"""Altium adapter + review pipeline (PURE transforms over eda-agent JSON).

IMPORTANT -- what these tests verify and what they do NOT:
* They verify our TRANSFORM LOGIC: that given a JSON shape, we map it to the IR correctly
  (units converted, fields renamed, dedup, DRC delta).
* They do NOT verify that eda-agent actually returns these shapes on Altium 26.7.1.11. The
  ``bom`` and ``drc`` shapes are from eda-agent's published docs; the ``rules`` and ``stackup``
  shapes are ASSUMED and must be confirmed against a real Phase-0 capture before trust.
No Altium and no network: ``review_board`` takes an injected ``check_fn``.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from altium_review import adapter, review  # noqa: E402

# eda-agent proj_get_bom excerpt (documented shape)
_BOM = {
    "bom": [
        {"designator": "U1", "mpn": "STM32F411CEU6", "value": "", "manufacturer": "ST"},
        {"designator": "C1", "mpn": "GRM155R61A106KA01L", "value": "10uF"},
        {"designator": "C2", "mpn": "GRM155R61A106KA01L", "value": "10uF"},  # dup MPN
        {"designator": "R9", "mpn": "", "value": "10k", "lcsc": "C25804"},  # sourced by LCSC
        "not-a-dict",  # tolerated
    ],
    "stats": {"total_components": 4},
}


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #
def test_mils_mm_roundtrip():
    assert abs(adapter.mils_to_mm(5) - 0.127) < 1e-9  # 5 mil == JLCPCB min track
    assert adapter.mils_to_mm(None) is None
    assert abs(adapter.mm_to_mils(0.127) - 5) < 1e-9


# --------------------------------------------------------------------------- #
# BOM (documented shape)
# --------------------------------------------------------------------------- #
def test_bom_to_parts_maps_and_tolerates_junk():
    parts = adapter.bom_to_parts(_BOM)
    by_ref = {p["ref"]: p for p in parts}
    assert set(by_ref) == {"U1", "C1", "C2", "R9"}  # the string row is skipped
    assert by_ref["U1"]["mpn"] == "STM32F411CEU6"
    assert by_ref["R9"]["lcsc"] == "C25804" and by_ref["R9"]["mpn"] == ""
    assert adapter.bom_to_parts({}) == []


# --------------------------------------------------------------------------- #
# DRC delta (documented shape) -- the edit guard
# --------------------------------------------------------------------------- #
def test_drc_delta_returns_only_new_violations():
    baseline = {
        "violations": [
            {
                "rule_name": "Clearance",
                "type": "clearance",
                "layer": "Top",
                "x": 10.0,
                "y": 20.0,
                "net1": "GND",
                "net2": "VCC",
            }
        ]
    }
    after = {
        "violations": [
            {
                "rule_name": "Clearance",
                "type": "clearance",
                "layer": "Top",
                "x": 10.0,
                "y": 20.0,
                "net1": "GND",
                "net2": "VCC",
            },  # pre-existing -> not the edit's fault
            {
                "rule_name": "Width",
                "type": "width",
                "layer": "Bottom",
                "x": 5.0,
                "y": 5.0,
                "net1": "SIG",
                "net2": None,
            },  # NEW
        ]
    }
    new = adapter.drc_delta(baseline, after)
    assert len(new) == 1 and new[0]["rule_name"] == "Width"


# --------------------------------------------------------------------------- #
# rules + stackup (ASSUMED shapes -- transform logic only)
# --------------------------------------------------------------------------- #
def test_rules_to_configured_keeps_tightest_floor_in_mm():
    rules = {
        "rules": [
            {"kind": "Clearance", "enabled": True, "min": 6},  # 6 mil
            {"kind": "Width", "enabled": True, "min": 5},  # 5 mil -> 0.127
            {"kind": "Width", "enabled": True, "min": 3.5},  # 3.5 mil -> tighter, wins
            {"kind": "HoleSize", "enabled": False, "min": 1},  # disabled -> ignored
            {"kind": "Unmapped", "enabled": True, "min": 9},  # no IR key -> dropped
        ]
    }
    out = adapter.rules_to_configured(rules, unit="mil")
    assert abs(out["min_clearance"] - 6 * 0.0254) < 1e-9
    assert abs(out["min_track_width"] - 3.5 * 0.0254) < 1e-9  # tightest of the two widths
    assert "min_through_hole_diameter" not in out  # disabled rule skipped


def test_stackup_to_layers_maps_types_and_converts():
    stk = {
        "layers": [
            {"type": "Signal", "thickness": 1.4},  # -> copper
            {"type": "Prepreg", "thickness": 8.28, "dielectric_constant": 4.4},
            {"type": "Core", "thickness": 41.9, "epsilon_r": 4.43},
        ]
    }
    layers = adapter.stackup_to_layers(stk, unit="mil")
    assert [layer["type"] for layer in layers] == ["copper", "prepreg", "core"]
    assert abs(layers[1]["thickness"] - 8.28 * 0.0254) < 1e-6
    assert layers[1]["epsilon_r"] == 4.4 and layers[2]["epsilon_r"] == 4.43


# --------------------------------------------------------------------------- #
# end-to-end review (hermetic: injected checker)
# --------------------------------------------------------------------------- #
def _fake_check(mpn, timeout=20.0):
    return {"valid": True, "available_on": ["digikey"], "jlcpcb": {}, "digikey": {"found": True}}


def test_review_board_combines_sourcing_and_manufacturability():
    board_info = {"copper_layers": 2, "copper_oz": 1.0, "thickness": 62.99}  # ~1.6 mm in mils
    geometry = {"min_track_width": 8, "min_via_drill": 14, "min_annular_ring": 6}  # mils
    r = review.review_board(
        bom=_BOM, board_info=board_info, geometry=geometry, unit="mil", check_fn=_fake_check
    )
    # sourcing: deduped to 3 distinct parts, all valid via the fake checker
    assert {p["part"] for p in r["sourcing"]["parts"]} == {
        "STM32F411CEU6",
        "GRM155R61A106KA01L",
        "C25804",
    }
    assert all(p["valid"] for p in r["sourcing"]["parts"])
    # manufacturability graded on mm-converted facts
    assert r["manufacturability"]["layers"] == 2
    assert abs(r["facts"]["min_track_width_mm"] - 8 * 0.0254) < 1e-6
    assert isinstance(r["manufacturability"]["manufacturable"], bool)


def test_review_board_handles_empty_inputs():
    r = review.review_board(check_fn=_fake_check)
    assert r["sourcing"] == {"parts": [], "missing_mpn": []}
    assert r["manufacturability"]["manufacturable"] is True  # nothing to violate
