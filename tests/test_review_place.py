"""Place-symbol engine: clone an existing instance into a new FLOATING symbol.

Pure-text tests (paren matching, span extraction, the clone itself) run anywhere. A
hermetic ``propose_place`` test fakes ERC so the guard path runs in CI. The real-board
tests are gated on a KiCad install + the PERIPH copy and never touch the live board.
"""

import os
from pathlib import Path
import re
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sexpdata  # noqa: E402

from kicad_mcp.edit import clone_instance, find_instance  # noqa: E402
from kicad_mcp.edit.locate import EditError, list_references  # noqa: E402
from kicad_mcp.edit.place import _instance_span, _match_paren  # noqa: E402

_BOARD = os.environ.get(
    "KICAD_REVIEW_TEST_PROJECT", r"C:/Users/jonny/Desktop/Trellis/In-Pipe-Hardware/v0/PERIPH"
)
_ROOT_UUID = "00000000-0000-0000-0000-0000000000aa"
_INST_UUID = "11111111-2222-3333-4444-555555555555"
_PIN1 = "aaaaaaaa-0000-0000-0000-000000000001"
_PIN2 = "aaaaaaaa-0000-0000-0000-000000000002"
_UUID_RX = re.compile(r'\(uuid "([0-9a-fA-F-]{36})"\)')

# a minimal but structurally faithful .kicad_sch: a root uuid, a lib_symbols cache
# entry, and ONE placed instance (R1) with two pins + an instances/path block.
_MINI = (
    "(kicad_sch\n"
    f'\t(uuid "{_ROOT_UUID}")\n'
    "\t(lib_symbols\n"
    '\t\t(symbol "Device:R"\n'
    '\t\t\t(property "Value" "R")\n'
    "\t\t)\n"
    "\t)\n"
    "\t(symbol\n"
    '\t\t(lib_id "Device:R")\n'
    "\t\t(at 10 20 0)\n"
    "\t\t(unit 1)\n"
    f'\t\t(uuid "{_INST_UUID}")\n'
    '\t\t(property "Reference" "R1"\n'
    "\t\t\t(at 12 19 0)\n"
    "\t\t)\n"
    '\t\t(property "Value" "10k"\n'
    "\t\t\t(at 12 21 0)\n"
    "\t\t)\n"
    '\t\t(property "Footprint" "Resistor_SMD:R_0603")\n'
    '\t\t(pin "1"\n'
    f'\t\t\t(uuid "{_PIN1}")\n'
    "\t\t)\n"
    '\t\t(pin "2"\n'
    f'\t\t\t(uuid "{_PIN2}")\n'
    "\t\t)\n"
    "\t\t(instances\n"
    '\t\t\t(project "PERIPH"\n'
    f'\t\t\t\t(path "/{_ROOT_UUID}"\n'
    '\t\t\t\t\t(reference "R1")\n'
    "\t\t\t\t\t(unit 1)\n"
    "\t\t\t\t)\n"
    "\t\t\t)\n"
    "\t\t)\n"
    "\t)\n"
    ")\n"
)


def _have_cli():
    from kicad_mcp.review import kicad

    try:
        kicad.find_kicad_cli()
        return True
    except kicad.KiCadError:
        return False


requires_board = pytest.mark.skipif(
    not Path(_BOARD).exists() or not _have_cli(), reason="needs kicad-cli + real board"
)


# --------------------------------------------------------------------------- #
# pure: string-aware paren matching
# --------------------------------------------------------------------------- #
def test_match_paren_simple():
    s = "(a (b) c)"
    assert _match_paren(s, 0) == len(s)
    assert _match_paren(s, 3) == 6  # the inner (b)


def test_match_paren_ignores_parens_inside_strings():
    s = '(a "x)y(z" b)'  # the parens inside the quoted string must not count
    assert _match_paren(s, 0) == len(s)


def test_match_paren_handles_escaped_quote_in_string():
    s = r'(a "x\")y" b)'  # the \" is a literal quote, not the string end
    assert _match_paren(s, 0) == len(s)


def test_match_paren_unbalanced_raises():
    with pytest.raises(EditError):
        _match_paren("(a (b)", 0)


# --------------------------------------------------------------------------- #
# pure: span extraction
# --------------------------------------------------------------------------- #
def test_instance_span_extracts_only_the_placed_block():
    start, end = _instance_span(_MINI, _INST_UUID)
    block = _MINI[start:end]
    assert block.startswith("\t(symbol\n")
    assert block.rstrip().endswith(")")
    assert f'(uuid "{_INST_UUID}")' in block
    assert '(lib_id "Device:R")' in block
    # the two-tab lib_symbols cache entry is NOT part of the placed-instance span
    assert '(property "Value" "R")' not in block


def test_instance_span_unknown_uuid_raises():
    with pytest.raises(EditError):
        _instance_span(_MINI, "deadbeef-0000-0000-0000-000000000000")


# --------------------------------------------------------------------------- #
# pure: clone_instance
# --------------------------------------------------------------------------- #
def test_clone_creates_a_floating_copy(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    assert clone_instance(sch, "R1", "R2", at=(50.0, 60.0)) == "R2"
    assert list_references(sch) == ["R1", "R2"]
    new = find_instance(sch, "R2")
    assert new is not None
    assert new.value == "10k"
    assert new.lib_id == "Device:R"
    assert new.footprint == "Resistor_SMD:R_0603"
    assert new.uuid != _INST_UUID  # fresh instance uuid
    sexpdata.loads(sch.read_text(encoding="utf-8"))  # still parses


def test_clone_regenerates_every_uuid_uniquely(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    before = _UUID_RX.findall(_MINI)  # root + instance + 2 pins
    clone_instance(sch, "R1", "R2", at=(50.0, 60.0))
    after = _UUID_RX.findall(sch.read_text(encoding="utf-8"))
    assert len(after) == len(before) + 3  # clone added instance + 2 pin uuids
    assert len(set(after)) == len(after)  # all distinct -> no pin-uuid collision
    assert set(before) <= set(after)  # originals untouched


def test_clone_sets_reference_at_both_sites(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    clone_instance(sch, "R1", "R2", at=(50.0, 60.0))
    text = sch.read_text(encoding="utf-8")
    assert '(property "Reference" "R2"' in text  # the symbol property
    assert '(reference "R2")' in text  # the instances/path back-reference


def test_clone_moves_placement_only(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    clone_instance(sch, "R1", "R2", at=(50.8, 60.96))  # already on the 1.27 mm grid
    text = sch.read_text(encoding="utf-8")
    assert "(at 50.8 60.96 0)" in text  # the clone's new origin
    assert "(at 10 20 0)" in text  # the source origin is preserved


def test_clone_snaps_off_grid_placement(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    clone_instance(sch, "R1", "R2", at=(50.0, 60.0))  # off the 1.27 mm grid
    text = sch.read_text(encoding="utf-8")
    # snapped to the nearest grid point so pins stay on-grid (avoids endpoint_off_grid ERC)
    assert "(at 49.53 59.69 0)" in text


def test_clone_colliding_reference_raises(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    with pytest.raises(EditError):
        clone_instance(sch, "R1", "R1", at=(1.0, 2.0))


def test_clone_missing_source_raises(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(_MINI, encoding="utf-8")
    with pytest.raises(EditError):
        clone_instance(sch, "R99", "R2", at=(1.0, 2.0))


# --------------------------------------------------------------------------- #
# hermetic guard: propose_place with ERC faked (runs in CI, no kicad-cli)
# --------------------------------------------------------------------------- #
def test_propose_place_dry_run_then_apply_hermetic(tmp_path, monkeypatch):
    from kicad_mcp.edit import guard
    from kicad_mcp.review import kicad

    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "PROJ.kicad_pro").write_text("{}", encoding="utf-8")
    (proj_dir / "PROJ.kicad_sch").write_text(_MINI, encoding="utf-8")
    # fake ERC: 0 errors, and crucially it RETURNS (so loads_ok is True)
    monkeypatch.setattr(kicad, "run_erc", lambda project, out=None: {"violations": []})

    proj = kicad.discover_project(proj_dir)
    live_before = Path(proj.sch).read_text(encoding="utf-8")

    res = guard.propose_place(proj, "R1", "R2", (50.0, 60.0), apply=False)
    assert res["applied"] is False
    assert res["loads_ok"] is True
    assert "R2" in res["diff"]
    assert res["at"] == [50.0, 60.0]
    assert "FLOATING" in res["note"]
    # dry run leaves the live schematic byte-identical
    assert Path(proj.sch).read_text(encoding="utf-8") == live_before

    res2 = guard.propose_place(proj, "R1", "R2", (50.0, 60.0), apply=True)
    assert res2["applied"] is True
    assert find_instance(proj.sch, "R2") is not None


def test_propose_place_does_not_apply_when_load_fails(tmp_path, monkeypatch):
    from kicad_mcp.edit import guard
    from kicad_mcp.review import kicad

    proj_dir = tmp_path / "PROJ"
    proj_dir.mkdir()
    (proj_dir / "PROJ.kicad_pro").write_text("{}", encoding="utf-8")
    (proj_dir / "PROJ.kicad_sch").write_text(_MINI, encoding="utf-8")
    # ERC fails to load the edited copy -> KiCadError -> _erc_error_count returns None
    monkeypatch.setattr(
        kicad, "run_erc", lambda project, out=None: (_ for _ in ()).throw(kicad.KiCadError("boom"))
    )

    proj = kicad.discover_project(proj_dir)
    live_before = Path(proj.sch).read_text(encoding="utf-8")
    res = guard.propose_place(proj, "R1", "R2", (50.0, 60.0), apply=True)
    assert res["loads_ok"] is False
    assert res["applied"] is False  # refused to write an unloadable schematic
    assert Path(proj.sch).read_text(encoding="utf-8") == live_before


# --------------------------------------------------------------------------- #
# integration: real PERIPH copy (never the live board)
# --------------------------------------------------------------------------- #
@requires_board
def test_clone_on_real_board(tmp_path):
    src = Path(_BOARD) / "PERIPH.kicad_sch"
    sch = tmp_path / "PERIPH.kicad_sch"
    shutil.copy(src, sch)
    refs = list_references(sch)
    source = next(r for r in refs if r.startswith("C"))
    new_ref = "C99999"
    assert new_ref not in refs
    clone_instance(sch, source, new_ref, at=(60.0, 60.0))
    assert find_instance(sch, new_ref) is not None
    assert find_instance(sch, source) is not None  # source preserved
    assert set(list_references(sch)) - set(refs) == {new_ref}  # exactly one new ref
    sexpdata.loads(sch.read_text(encoding="utf-8"))  # still parses


@requires_board
def test_propose_place_dry_run_then_apply_on_board(tmp_path):
    from kicad_mcp.edit.guard import propose_place
    from kicad_mcp.review import kicad

    dst = tmp_path / "PERIPH"
    shutil.copytree(
        _BOARD, dst, ignore=shutil.ignore_patterns(".kicad-review", "*-backups", "_autosave*", "~*")
    )
    proj = kicad.discover_project(dst)
    source = next(r for r in list_references(proj.sch) if r.startswith("C"))
    live_before = Path(proj.sch).read_text(encoding="utf-8")

    res = propose_place(proj, source, "C99999", (60.0, 60.0), apply=False)
    assert res["applied"] is False
    assert res["loads_ok"] is True
    assert "C99999" in res["diff"]
    assert Path(proj.sch).read_text(encoding="utf-8") == live_before  # untouched

    res2 = propose_place(proj, source, "C99999", (60.0, 60.0), apply=True)
    assert res2["applied"] is True
    assert find_instance(proj.sch, "C99999") is not None
    # a floating part never REDUCES the ERC error count
    assert res2["erc_after"] >= res2["erc_before"]
