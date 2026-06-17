"""Sweep a whole schematic's sourcing: pull every component's MPN/LCSC and check stock.

``extract_parts`` reads each placed symbol's properties (MPN / LCSC by their common field
names), recursing into hierarchical sub-sheets; ``check_bom`` de-duplicates by part number
and checks each on JLCPCB + DigiKey in parallel, so one command answers "is my entire BOM
orderable and in stock?".
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sexpdata

from kicad_mcp.parts.pull import PartSourceError
from kicad_mcp.parts.stock import check_stock
from kicad_mcp.review.parse import _getall, _head, _sym

# field names a KiCad symbol might carry an MPN / LCSC code under (matched case-insensitively)
_MPN_FIELDS = (
    "MPN",
    "Manufacturer Part Number",
    "Mfr Part #",
    "MfrPN",
    "Manufacturer_Part_Number",
    "Mfr. No",
    "Part Number",
    "VPN",
)
_LCSC_FIELDS = ("LCSC", "LCSC Part #", "LCSC Part Number", "LCSC#", "JLCPCB Part #")


def _properties(node) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in _getall(node, "property"):
        if isinstance(p, list) and len(p) >= 3:
            out[str(_sym(p[1]))] = str(_sym(p[2]))
    return out


def _pick(props: dict[str, str], fields) -> str:
    lower = {k.lower(): v for k, v in props.items()}
    for f in fields:
        v = lower.get(f.lower())
        if v:
            return v
    return ""


def _walk(path: Path, seen: set, out: list[dict], is_root: bool) -> None:
    """Collect placed components from ``path`` and, recursively, its sub-sheet files."""
    p = path.resolve()
    if p in seen:
        return
    if not p.is_file():
        if is_root:
            raise PartSourceError(f"schematic not found: {path}")
        return  # a referenced sub-sheet that is missing -> skip, don't abort the sweep
    seen.add(p)
    try:
        data = sexpdata.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        if is_root:
            raise PartSourceError(f"could not parse {p.name}: {e}") from e
        return  # a malformed sub-sheet -> skip
    for node in data[1:] if isinstance(data, list) else []:
        h = _head(node)
        if h == "symbol":
            props = _properties(node)
            ref = props.get("Reference", "")
            if ref.startswith("#"):  # power/virtual symbols (#PWR, #FLG) only
                continue
            out.append(
                {
                    "ref": ref or "?",  # surface a real part even if it lacks a Reference
                    "value": props.get("Value", ""),
                    "mpn": _pick(props, _MPN_FIELDS),
                    "lcsc": _pick(props, _LCSC_FIELDS),
                }
            )
        elif h == "sheet":
            sheetfile = _properties(node).get("Sheetfile")
            if sheetfile:
                _walk(p.parent / sheetfile, seen, out, is_root=False)


def extract_parts(sch_path: str | Path) -> list[dict]:
    """[{ref, value, mpn, lcsc}] for every placed (non-power) component in the design,
    recursing through hierarchical sub-sheets. Raises PartSourceError on a missing/unparseable
    root schematic."""
    out: list[dict] = []
    _walk(Path(sch_path), set(), out, is_root=True)
    return out


def check_bom(sch_path: str | Path, timeout: float = 20.0, max_workers: int = 6) -> dict:
    """Check every distinct MPN/LCSC in the schematic on JLCPCB + DigiKey.

    Returns ``{parts: [{part, value, refs, jlcpcb, digikey}], missing_mpn: [{ref, value}]}``.
    ``missing_mpn`` lists components that carry no MPN/LCSC field at all -- an unsourced-part
    gap worth surfacing.
    """
    parts = extract_parts(sch_path)
    uniq: dict[str, dict] = {}
    missing: list[dict] = []
    for p in parts:
        key = (p["mpn"] or p["lcsc"]).strip()
        if not key:
            missing.append({"ref": p["ref"], "value": p["value"]})
            continue
        slot = uniq.setdefault(key.upper(), {"part": key, "value": p["value"], "refs": []})
        slot["refs"].append(p["ref"])

    def _one(info: dict) -> dict:
        try:
            res = check_stock(info["part"], timeout=timeout)
            return {
                **info,
                "valid": res["valid"],
                "available_on": res["available_on"],
                "jlcpcb": res["jlcpcb"],
                "digikey": res["digikey"],
            }
        except Exception as e:  # noqa: BLE001 - one bad part must not abort the whole sweep
            return {
                **info,
                "valid": False,
                "available_on": [],
                "jlcpcb": {"error": f"{type(e).__name__}: {e}"},
                "digikey": {},
            }

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        checked = list(ex.map(_one, uniq.values()))
    return {"parts": checked, "missing_mpn": missing}
