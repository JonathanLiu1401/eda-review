"""Fabrication exports + fab-readiness assessment (read-only).

Produces the deliverables a board shop / assembler needs (Gerbers, drill, pick-and-place,
STEP) and grades whether the board is actually ready to send out. This is the natural
extension of a review agent: it packages and assesses the handoff -- it never authors layout.
"""

from __future__ import annotations

from pathlib import Path

from kicad_mcp.review import kicad

# An Edge.Cuts Gerber with a real outline is well above this; an empty one is just header/footer.
_MIN_EDGE_GERBER_BYTES = 350


def export_fab_package(project: kicad.Project, out: str | None = None) -> dict:
    """Produce the full fab handoff. Returns the paths of each deliverable."""
    return {
        "gerbers": str(kicad.export_gerbers(project, out)),
        "drill": str(kicad.export_drill(project, out)),
        "pick_and_place": str(kicad.export_pos(project, out)),
        "step": str(kicad.export_step(project, out)),
    }


def _drc_error_count(project: kicad.Project) -> int | None:
    """Number of non-excluded error-severity DRC violations, or None if DRC could not run."""
    try:
        drc = kicad.run_drc(project, parity=False)
    except kicad.KiCadError:
        return None
    viol = drc.get("violations", []) or []
    return sum(1 for v in viol if v.get("severity") == "error" and not v.get("excluded"))


def _has_board_outline(gerber_dir: str) -> bool:
    edge = [f for f in Path(gerber_dir).iterdir() if "Edge_Cuts" in f.name]
    return bool(edge) and any(f.stat().st_size >= _MIN_EDGE_GERBER_BYTES for f in edge)


def check_fab_readiness(project: kicad.Project, out: str | None = None) -> dict:
    """Produce the fab package AND grade whether the board is ready to fabricate.

    Returns ``{ready, drc_errors, findings: [{severity, title, detail}], package: {...}}``.
    ``ready`` is False when any blocker is present (DRC errors, or no board outline) -- a
    review verdict, not an edit.
    """
    if not project.pcb:
        raise kicad.KiCadError("No PCB to check for fabrication.")
    findings: list[dict] = []

    errors = _drc_error_count(project)
    if errors:
        findings.append(
            {
                "severity": "blocker",
                "title": f"{errors} DRC error(s) -- fix before fabrication",
                "detail": "Run `drc` for the full list. Boards with DRC errors are commonly "
                "rejected or fabricated with defects.",
            }
        )

    package = export_fab_package(project, out)

    if not _has_board_outline(package["gerbers"]):
        findings.append(
            {
                "severity": "blocker",
                "title": "No board outline on Edge.Cuts",
                "detail": "The fab cannot determine the board shape/size without an Edge.Cuts "
                "outline. Draw the board edge before exporting.",
            }
        )

    ready = not any(f["severity"] == "blocker" for f in findings)
    return {"ready": ready, "drc_errors": errors, "findings": findings, "package": package}
