"""Place a symbol instance by cloning an existing one (place-but-don't-wire).

Parse-valid by construction: copies a placed ``(symbol ...)`` block that KiCad already
accepts (its ``lib_symbols`` definition is already cached), then regenerates the
instance UUID + every per-pin UUID, sets a fresh non-colliding Reference (dual-site:
the property AND the ``instances/path`` reference), and a new position. The placed part
is FLOATING (unconnected) -- wiring is a separate, GUI/connectivity-aware step.
"""

from __future__ import annotations

from pathlib import Path
import re
import uuid

from kicad_mcp.edit.locate import EditError, find_instance

_UUID_RE = re.compile(r'\(uuid "[0-9a-fA-F-]{36}"\)')
_AT_RE = re.compile(r"\(at -?[\d.]+ -?[\d.]+( -?[\d.]+)?\)")
_REF_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")  # a safe refdes: letter then alphanumerics/_
_GRID_MM = 1.27  # KiCad's default schematic grid (50 mil); ERC flags off-grid pin endpoints


def _snap(v: float) -> float:
    """Snap a coordinate to the 1.27 mm schematic grid so cloned pins stay on-grid
    (an arbitrary placement coordinate otherwise trips ERC's endpoint_off_grid check)."""
    return round(round(v / _GRID_MM) * _GRID_MM, 4)


def _reposition(block: str, at: tuple[float, float]) -> str:
    """Move the symbol to ``at`` (grid-snapped), shifting the origin AND every property-label
    ``(at)`` by the same delta so the labels track the body (KiCad stores label positions as
    absolute coordinates), and preserving the symbol's rotation."""
    m = _AT_RE.search(block)  # the first (at ...) is the symbol origin
    if not m:
        return block
    o = m.group(0)[4:-1].split()
    dx, dy = _snap(at[0]) - float(o[0]), _snap(at[1]) - float(o[1])

    def _shift(mm: re.Match) -> str:
        t = mm.group(0)[4:-1].split()
        rot = f" {t[2]}" if len(t) > 2 else ""
        return f"(at {round(float(t[0]) + dx, 4)} {round(float(t[1]) + dy, 4)}{rot})"

    return _AT_RE.sub(_shift, block)


def _match_paren(text: str, i: int) -> int:
    """Index just past the ``)`` matching the ``(`` at ``text[i]`` (string-aware)."""
    if text[i] != "(":
        raise EditError("expected '(' at scan start")
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
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise EditError("unbalanced parentheses while scanning a (symbol ...) block")


def _instance_span(text: str, inst_uuid: str) -> tuple[int, int]:
    """(start, end) byte span of the placed ``(symbol ...)`` block carrying ``inst_uuid``."""
    ai = text.find(f'(uuid "{inst_uuid}")')
    if ai < 0:
        raise EditError(f"instance uuid {inst_uuid} not found")
    start = text.rfind("\n\t(symbol\n", 0, ai)
    if start < 0:
        raise EditError("could not locate the enclosing placed (symbol ...) block")
    start += 1  # past the leading newline -> the '\t(symbol' line
    return start, _match_paren(text, text.index("(", start))


def clone_instance(
    sch_path: str | Path, source_ref: str, new_ref: str, at: tuple[float, float]
) -> str:
    """Clone the placed instance ``source_ref`` to a new floating instance ``new_ref`` at
    ``at`` (x, y mm, snapped to the 1.27 mm grid). Returns ``new_ref``. Raises EditError on
    a reference collision or a missing source."""
    if not _REF_RE.fullmatch(new_ref):
        raise EditError(
            f"unsafe reference {new_ref!r}: a refdes must be a letter followed by "
            "letters/digits/underscores (no quotes, spaces, or parentheses)"
        )
    if find_instance(sch_path, new_ref) is not None:
        raise EditError(f"reference {new_ref!r} already exists in the schematic")
    src = find_instance(sch_path, source_ref)
    if src is None:
        raise EditError(f"no placed instance with Reference {source_ref!r} to clone")

    path = Path(sch_path)
    text = path.read_text(encoding="utf-8")
    start, end = _instance_span(text, src.uuid)
    block = text[start:end]

    # fresh UUIDs for the instance and every pin
    block = _UUID_RE.sub(lambda m: f'(uuid "{uuid.uuid4()}")', block)
    # new reference, both sites
    block = re.sub(
        r'(\(property "Reference" ")[^"]*(")',
        lambda m: m.group(1) + new_ref + m.group(2),
        block,
        count=1,
    )
    block = re.sub(
        r'(\(reference ")[^"]*(")',
        lambda m: m.group(1) + new_ref + m.group(2),
        block,
        count=1,
    )
    # move to the new (grid-snapped) origin, carrying the property labels + rotation along
    block = _reposition(block, at)

    new_text = text[:end] + "\n" + block + text[end:]
    path.write_text(new_text, encoding="utf-8")
    return new_ref
