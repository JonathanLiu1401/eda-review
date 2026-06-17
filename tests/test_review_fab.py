"""Fabrication exports + readiness assessment.

The board-outline detection is pure and runs anywhere; the real kicad-cli exports and the
readiness check are gated on a KiCad install + the board.
"""

import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kicad_mcp.review import fab, kicad  # noqa: E402

_BOARD = os.environ.get(
    "KICAD_REVIEW_TEST_PROJECT", r"C:/Users/jonny/Desktop/Trellis/In-Pipe-Hardware/v0/PERIPH"
)


def _have_cli():
    try:
        kicad.find_kicad_cli()
        return True
    except kicad.KiCadError:
        return False


requires_board = pytest.mark.skipif(
    not Path(_BOARD).exists() or not _have_cli(), reason="needs kicad-cli + real board"
)


# --------------------------------------------------------------------------- #
# pure: board-outline detection
# --------------------------------------------------------------------------- #
def test_has_board_outline_true(tmp_path):
    (tmp_path / "B-Edge_Cuts.gm1").write_text(
        "G04 outline*\n" + "X1Y1D02*\n" * 100, encoding="utf-8"
    )
    assert fab._has_board_outline(str(tmp_path)) is True


def test_has_board_outline_empty_is_false(tmp_path):
    (tmp_path / "B-Edge_Cuts.gm1").write_text(
        "G04*\n", encoding="utf-8"
    )  # header only -> no outline
    assert fab._has_board_outline(str(tmp_path)) is False


def test_has_board_outline_missing_is_false(tmp_path):
    (tmp_path / "B-F_Cu.gbr").write_text("x" * 999, encoding="utf-8")  # a layer, but no Edge.Cuts
    assert fab._has_board_outline(str(tmp_path)) is False


# --------------------------------------------------------------------------- #
# integration: real board (never mutated -- exports go to tmp)
# --------------------------------------------------------------------------- #
@requires_board
def test_fab_exports_produce_files(tmp_path):
    proj = kicad.discover_project(_BOARD)
    gerbers = kicad.export_gerbers(proj, str(tmp_path))
    assert any("Edge_Cuts" in f.name for f in Path(gerbers).iterdir())
    assert kicad.export_drill(proj, str(tmp_path)).is_dir()
    assert kicad.export_pos(proj, str(tmp_path)).is_file()
    assert kicad.export_step(proj, str(tmp_path)).is_file()


@requires_board
def test_fab_readiness(tmp_path):
    proj = kicad.discover_project(_BOARD)
    r = fab.check_fab_readiness(proj, str(tmp_path))
    assert set(r["package"]) == {"gerbers", "drill", "pick_and_place", "step"}
    assert isinstance(r["ready"], bool)
    assert isinstance(r["drc_errors"], int)
    # any DRC errors must produce a blocker finding and a not-ready verdict
    if r["drc_errors"]:
        assert r["ready"] is False
        assert any(f["severity"] == "blocker" for f in r["findings"])
