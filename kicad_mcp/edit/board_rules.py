"""Guarded write of JLCPCB's authoritative design rules into a ``.kicad_pro``.

Raises only the rules that are *looser* than JLCPCB's published minimums (``max(current,
floor)``), so KiCad's own DRC starts enforcing what JLCPCB can actually make. Edits are
surgical (regex number-replace) and scoped to the ``design_settings.rules`` block -- some rule
keys (e.g. ``min_clearance``) also appear in other blocks, so a whole-file replace would hit
the wrong one. No full-file JSON resave (which would reorder/reformat everything).
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re

from kicad_mcp.edit.locate import EditError
from kicad_mcp.review import jlcpcb
from kicad_mcp.review.parse import parse_board

# .kicad_pro rule key -> JLCPCB capability key
_RULE_TO_LIMIT = {
    "min_track_width": "min_track_width",
    "min_clearance": "min_clearance",
    "min_via_diameter": "min_via_diameter",
    "min_via_annular_width": "min_annular_ring",
    "min_through_hole_diameter": "min_pth",
    "min_copper_edge_clearance": "min_copper_edge_clearance",
}


def _match_brace(text: str, i: int) -> int:
    """Index just past the ``}`` matching the ``{`` at ``text[i]`` (string-aware)."""
    if text[i] != "{":
        raise EditError("expected '{' at scan start")
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(text):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise EditError("unbalanced braces while scanning the rules block")


def _rules_span(text: str) -> tuple[int, int]:
    """(start, end) of the ``design_settings.rules`` ``{...}`` object."""
    m = re.search(r'"rules"\s*:\s*\{', text)
    if not m:
        raise EditError('no "rules" block found in the .kicad_pro')
    brace = text.index("{", m.start())
    return brace, _match_brace(text, brace)


def _set_number(block: str, key: str, value: float) -> str:
    pat = re.compile(r'("' + re.escape(key) + r'"\s*:\s*)(-?[\d.]+)')
    return pat.sub(lambda m: m.group(1) + f"{value:g}", block, count=1)


def propose_jlcpcb_rules(project, apply: bool = False) -> dict:
    """Propose (or apply) raising the board's design rules to JLCPCB's minimums.

    Returns ``{changes:[{rule,old,new}], diff, applied, sources, verified}``. The live
    ``.kicad_pro`` changes only when ``apply`` is True and the edited file is valid JSON whose
    rules round-trip to the intended values.
    """
    if not project.pro:
        raise EditError("project has no .kicad_pro to write design rules into")
    pro = Path(project.pro)
    text = pro.read_text(encoding="utf-8")
    data = json.loads(text)
    rules = (data.get("board") or {}).get("design_settings", {}).get("rules", {}) or {}

    board = parse_board(project.pcb) if project.pcb else None
    limits = jlcpcb.capabilities_for(
        getattr(board, "copper_layers", None), getattr(board, "copper_oz", None)
    )

    bstart, bend = _rules_span(text)
    block = text[bstart:bend]
    changes: list[dict] = []
    for rule_key, limit_key in _RULE_TO_LIMIT.items():
        cur = rules.get(rule_key)
        floor = limits[limit_key]
        if isinstance(cur, int | float) and cur < floor - 1e-6:
            block = _set_number(block, rule_key, floor)
            changes.append({"rule": rule_key, "old": cur, "new": floor})

    new_text = text[:bstart] + block + text[bend:]

    # gate 1: still valid JSON
    new_data = json.loads(new_text)
    # gate 2: round-trip -- the rules actually hold the intended values
    new_rules = (new_data.get("board") or {}).get("design_settings", {}).get("rules", {})
    for ch in changes:
        got = new_rules.get(ch["rule"])
        if got is None or abs(float(got) - ch["new"]) > 1e-9:
            raise EditError(
                f"round-trip check failed for {ch['rule']}: wanted {ch['new']}, got {got}"
            )

    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="design rules (before)",
            tofile="design rules (after, JLCPCB minimums)",
        )
    )

    applied = False
    if apply and changes:
        tmp = pro.with_name(pro.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, pro)  # atomic
        applied = True

    return {
        "changes": changes,
        "diff": diff,
        "applied": applied,
        "sources": list(jlcpcb.SOURCES),
        "verified": jlcpcb.VERIFIED,
    }
