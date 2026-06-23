"""EDA-neutral shared core: JLCPCB grading on the IR + the distributor sweep (check_parts).

All hermetic -- ``grade_jlcpcb`` takes a ``BoardFacts`` directly (no board, no kicad-cli) and
``check_parts`` takes an injected checker (no network). These are the tests that keep
``eda_core`` covered now that the grading/sourcing logic moved out of the KiCad package.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eda_core import jlcpcb  # noqa: E402
from eda_core.ir import BoardFacts  # noqa: E402
from eda_core.parts import check_parts  # noqa: E402

# the JLCPCB 4L/1.6mm reference stack, reused as a "clean" board stackup
_REF_LAYERS = jlcpcb.STACKUPS[(4, 1.6)]["layers"]
# configured rules exactly at the 4-layer floors -> not "looser" (equal is fine)
_CLEAN_RULES = {
    "min_copper_edge_clearance": 0.30,
    "min_through_hole_diameter": 0.15,
    "min_track_width": 0.09,
    "min_clearance": 0.09,
    "min_via_diameter": 0.45,
    "min_via_annular_width": 0.15,
}


def _clean_facts(**over) -> BoardFacts:
    base = {
        "copper_layers": 4,
        "copper_oz": 1.0,
        "thickness_mm": 1.6,
        "min_track_width": 0.20,
        "min_via_drill": 0.30,
        "min_annular_ring": 0.15,
        "configured_rules": dict(_CLEAN_RULES),
        "stackup": list(_REF_LAYERS),
    }
    base.update(over)
    return BoardFacts(**base)


# --------------------------------------------------------------------------- #
# grade_jlcpcb
# --------------------------------------------------------------------------- #
def test_clean_board_is_manufacturable_with_no_findings():
    r = jlcpcb.grade_jlcpcb(_clean_facts())
    assert r["manufacturable"] is True
    assert r["findings"] == []
    assert r["layers"] == 4 and r["copper_oz"] == 1.0 and r["thickness_mm"] == 1.6
    assert r["sources"] and r["verified"]


def test_thin_track_is_a_blocker():
    r = jlcpcb.grade_jlcpcb(_clean_facts(min_track_width=0.05))
    assert r["manufacturable"] is False
    assert any(f["severity"] == "blocker" and "track" in f["title"].lower() for f in r["findings"])


def test_small_drill_and_ring_are_blockers():
    rd = jlcpcb.grade_jlcpcb(_clean_facts(min_via_drill=0.10))
    rr = jlcpcb.grade_jlcpcb(_clean_facts(min_annular_ring=0.05))
    assert rd["manufacturable"] is False and rr["manufacturable"] is False
    assert any("drill" in f["title"].lower() for f in rd["findings"])
    assert any("annular" in f["title"].lower() for f in rr["findings"])


def test_looser_configured_rule_is_major_not_blocker():
    facts = _clean_facts(configured_rules={**_CLEAN_RULES, "min_track_width": 0.05})
    r = jlcpcb.grade_jlcpcb(facts)
    assert r["manufacturable"] is True  # the fab can still build it
    assert any(f["severity"] == "major" and "min track width" in f["title"] for f in r["findings"])


def test_nonstandard_copper_and_thickness_are_major():
    # 2-layer so there is no reference stackup to also fire on thickness
    r = jlcpcb.grade_jlcpcb(
        _clean_facts(copper_layers=2, copper_oz=3.0, thickness_mm=1.55, stackup=[])
    )
    titles = " ".join(f["title"] for f in r["findings"])
    assert "Copper weight" in titles and "thickness" in titles
    assert all(f["severity"] != "blocker" for f in r["findings"])


def test_missing_stackup_when_reference_exists_is_major():
    r = jlcpcb.grade_jlcpcb(_clean_facts(stackup=[]))
    assert any("No explicit stackup" in f["title"] for f in r["findings"])


def test_stackup_dielectric_mismatch_is_major():
    bad = [dict(layer) for layer in _REF_LAYERS]
    bad[1] = {**bad[1], "thickness": 0.1}  # generic prepreg instead of JLCPCB's 0.2104
    r = jlcpcb.grade_jlcpcb(_clean_facts(stackup=bad))
    assert any("differ from JLCPCB" in f["title"] for f in r["findings"])


def test_no_reference_stackup_on_4plus_layer_is_minor():
    r = jlcpcb.grade_jlcpcb(_clean_facts(copper_layers=6, stackup=[]))
    assert any(
        f["severity"] == "minor" and "reference stackup" in f["title"] for f in r["findings"]
    )


def test_capabilities_tiers_via_core():
    assert jlcpcb.capabilities_for(2, 1)["min_track_width"] == 0.127
    assert jlcpcb.capabilities_for(4, 1)["min_track_width"] == 0.09
    assert jlcpcb.capabilities_for(4, 2)["min_track_width"] == 0.20  # 2 oz widens
    assert jlcpcb.reference_stackup(4, 1.6)["code"] == "JLC04161H-7628"
    assert jlcpcb.reference_stackup(2, 1.6) is None


# --------------------------------------------------------------------------- #
# check_parts (distributor sweep with an injected checker)
# --------------------------------------------------------------------------- #
def _ok(mpn, timeout=20.0):
    return {"valid": True, "available_on": ["jlcpcb"], "jlcpcb": {"found": True}, "digikey": {}}


def test_check_parts_dedupes_and_flags_missing():
    parts = [
        {"ref": "R1", "value": "10k", "mpn": "AAA", "lcsc": ""},
        {"ref": "R2", "value": "1k", "mpn": "", "lcsc": ""},  # no MPN/LCSC -> missing
        {"ref": "R3", "value": "10k", "mpn": "AAA", "lcsc": ""},  # dup of R1 -> merged refs
        {"ref": "C1", "value": "100nF", "mpn": "", "lcsc": "C1525"},  # sourced by LCSC
    ]
    res = check_parts(parts, _ok)
    by_part = {p["part"]: p for p in res["parts"]}
    assert set(by_part) == {"AAA", "C1525"}
    assert sorted(by_part["AAA"]["refs"]) == ["R1", "R3"]
    assert [m["ref"] for m in res["missing_mpn"]] == ["R2"]
    assert all(p["valid"] for p in res["parts"])


def test_check_parts_one_bad_part_does_not_abort():
    def fake(mpn, timeout=20.0):
        if mpn == "BAD":
            raise RuntimeError("boom")
        return _ok(mpn)

    res = check_parts(
        [
            {"ref": "U1", "value": "", "mpn": "BAD", "lcsc": ""},
            {"ref": "U2", "value": "", "mpn": "GOOD", "lcsc": ""},
        ],
        fake,
    )
    by_part = {p["part"]: p for p in res["parts"]}
    assert by_part["GOOD"]["valid"] is True
    assert by_part["BAD"]["valid"] is False and "error" in by_part["BAD"]["jlcpcb"]


# --------------------------------------------------------------------------- #
# IR
# --------------------------------------------------------------------------- #
def test_board_facts_defaults_are_empty_not_shared():
    a, b = BoardFacts(), BoardFacts()
    a.configured_rules["x"] = 1
    a.stackup.append({})
    assert b.configured_rules == {} and b.stackup == []  # default_factory, not a shared mutable
