"""Surgical (byte-span) edits to a ``.kicad_sch``.

Changes exactly one property value of one placed instance, anchored on that instance's
globally-unique ``(uuid "...")``, leaving every other byte of the file unchanged. The
file is never deserialized-and-resaved, so no KiCad-10-only construct can be dropped.
"""

from __future__ import annotations

from pathlib import Path
import re

from kicad_mcp.edit.locate import EditError, find_instance


def _esc(s: str) -> str:
    """Escape a string for a KiCad quoted token."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _locate_property(text: str, uuid: str, prop_name: str) -> re.Match:
    """The ``(property "<name>" "<old>"`` match for ``prop_name`` on the instance ``uuid``.
    Anchors on the instance uuid first (the lib_symbols cache sits earlier, never matched)."""
    ai = text.find(f'(uuid "{uuid}")')
    if ai < 0:
        raise EditError(f"instance uuid {uuid} not found in file")
    pat = re.compile(r'\(property "' + re.escape(prop_name) + r'" "((?:[^"\\]|\\.)*)"')
    m = pat.search(text, ai)
    if not m:
        raise EditError(f'property "{prop_name}" not found for instance {uuid}')
    return m


def edit_property_text(text: str, uuid: str, prop_name: str, new_value: str) -> str:
    """Pure (no I/O): return ``text`` with the given instance's ``prop_name`` value
    replaced by ``new_value``."""
    m = _locate_property(text, uuid, prop_name)
    return text[: m.start(1)] + _esc(new_value) + text[m.end(1) :]


def set_property(sch_path: str | Path, reference: str, prop_name: str, new_value: str) -> str:
    """Set one property on the placed instance ``reference`` in place. Returns the true old
    value (for ANY property, not just Value/Footprint). Raises EditError if the instance or
    property is not found."""
    inst = find_instance(sch_path, reference)
    if inst is None:
        raise EditError(f"no placed instance with Reference {reference!r}")
    path = Path(sch_path)
    text = path.read_text(encoding="utf-8")
    old = _locate_property(text, inst.uuid, prop_name).group(1)
    new_text = edit_property_text(text, inst.uuid, prop_name, new_value)
    path.write_text(new_text, encoding="utf-8")
    return old


def set_value(sch_path: str | Path, reference: str, value: str) -> str:
    return set_property(sch_path, reference, "Value", value)


def set_footprint(sch_path: str | Path, reference: str, footprint: str) -> str:
    return set_property(sch_path, reference, "Footprint", footprint)
