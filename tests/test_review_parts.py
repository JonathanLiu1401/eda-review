"""Part sourcing: installed-library lookup + MPN->LCSC->easyeda2kicad pull.

Pure tests run anywhere. Local-library tests are gated on a KiCad install. The live
online pull is gated behind ``KICAD_REVIEW_NETWORK_TESTS=1`` so CI stays hermetic.
"""

import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kicad_mcp.parts import find_part  # noqa: E402
from kicad_mcp.parts import local as plocal  # noqa: E402
from kicad_mcp.parts import pull as ppull  # noqa: E402


def _have_libs():
    return bool(plocal.symbol_dirs())


requires_libs = pytest.mark.skipif(
    not _have_libs(), reason="needs installed KiCad symbol libraries"
)
network = pytest.mark.skipif(
    os.environ.get("KICAD_REVIEW_NETWORK_TESTS") != "1",
    reason="set KICAD_REVIEW_NETWORK_TESTS=1 to run live online pulls",
)


# --------------------------------------------------------------------------- #
# pure: jlcsearch response parsing
# --------------------------------------------------------------------------- #
def test_parse_lcsc_response_picks_first():
    data = {"components": [{"lcsc": 22397078, "mfr": "DRV8234RTER"}, {"lcsc": 1, "mfr": "x"}]}
    assert ppull.parse_lcsc_response(data) == "C22397078"


def test_parse_lcsc_response_empty():
    assert ppull.parse_lcsc_response({"components": []}) is None
    assert ppull.parse_lcsc_response({}) is None


def test_have_easyeda2kicad_is_bool():
    assert isinstance(ppull.have_easyeda2kicad(), bool)


def test_pull_lcsc_raises_without_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(ppull, "have_easyeda2kicad", lambda: False)
    with pytest.raises(ppull.PartSourceError):
        ppull.pull_lcsc("C123", str(tmp_path / "x"))


# --------------------------------------------------------------------------- #
# local: installed KiCad libraries
# --------------------------------------------------------------------------- #
@requires_libs
def test_symbol_dirs_exist():
    dirs = plocal.symbol_dirs()
    assert dirs and all(d.is_dir() for d in dirs)


@requires_libs
@pytest.mark.parametrize("part", ["1N4148", "BSS84"])
def test_find_known_symbol(part):
    hits = plocal.find_symbol(part)
    assert any(part in h for h in hits), hits
    # the id is "LibStem:SymbolName"
    assert all(":" in h for h in hits)


@requires_libs
def test_find_symbol_subunits_excluded():
    # a real part name, never a "_0_1" graphic sub-symbol
    hits = plocal.find_symbol("1N4148")
    assert not any(h.rsplit(":", 1)[-1].endswith(("_0_1", "_1_1")) for h in hits)


@requires_libs
def test_find_symbol_absent_is_empty():
    assert plocal.find_symbol("definitely_not_a_real_symbol_xyz") == []


# --------------------------------------------------------------------------- #
# source chain
# --------------------------------------------------------------------------- #
@requires_libs
def test_find_part_local_hit():
    res = find_part("1N4148")
    assert res["source"] == "local"
    assert any("1N4148" in s for s in res["symbols"])


def test_find_part_not_found_suggests_pull():
    res = find_part("ZZ_not_a_part_9999", do_pull=False)
    assert res["source"] == "not_found"
    assert "pull-part" in res["suggestion"]


# --------------------------------------------------------------------------- #
# live online pull (opt-in)
# --------------------------------------------------------------------------- #
@network
def test_resolve_lcsc_live():
    assert (ppull.resolve_lcsc("DRV8234RTER") or "").startswith("C")


@network
@pytest.mark.skipif(
    not ppull.have_easyeda2kicad(),
    reason="needs easyeda2kicad installed (pip install easyeda2kicad)",
)
def test_pull_mpn_live(tmp_path):
    res = ppull.pull_mpn("DRV8234RTER", str(tmp_path / "drv"))
    assert Path(res["symbol"]).is_file()
    assert res["mpn"] == "DRV8234RTER"
