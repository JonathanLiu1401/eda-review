"""BOM sourcing sweep: de-duplicate a list of parts and check each on the distributors.

EDA-neutral: the caller supplies the parts (each ``{ref, value, mpn, lcsc}``) and the
distributor checker as ``check_fn`` -- the KiCad backend extracts parts from a schematic,
the Altium backend gets them from eda-agent's ``proj_get_bom``, and both pass
:func:`eda_core.stock.check_stock`. Injecting ``check_fn`` keeps this module free of any
backend (and network) import, and lets tests pass a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


def check_parts(
    parts: list[dict],
    check_fn: Callable[..., dict],
    timeout: float = 20.0,
    max_workers: int = 6,
) -> dict:
    """De-duplicate ``parts`` by MPN/LCSC and check each distinct part with ``check_fn``.

    Returns ``{parts: [{part, value, refs, valid, available_on, jlcpcb, digikey}],
    missing_mpn: [{ref, value}]}``. ``missing_mpn`` lists components carrying no MPN/LCSC
    at all (an unsourced-part gap). One part raising never aborts the sweep -- it degrades
    to a structured error entry.
    """
    uniq: dict[str, dict] = {}
    missing: list[dict] = []
    for p in parts:
        key = (p.get("mpn") or p.get("lcsc") or "").strip()
        if not key:
            missing.append({"ref": p.get("ref", "?"), "value": p.get("value", "")})
            continue
        slot = uniq.setdefault(key.upper(), {"part": key, "value": p.get("value", ""), "refs": []})
        slot["refs"].append(p.get("ref", "?"))

    def _one(info: dict) -> dict:
        try:
            res = check_fn(info["part"], timeout=timeout)
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
