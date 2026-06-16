"""The tiered part-sourcing chain: local libraries first, then an online pull."""

from __future__ import annotations

from kicad_mcp.parts import local, pull
from kicad_mcp.parts.pull import PartSourceError  # noqa: F401  (re-export)


def find_part(query: str, do_pull: bool = False, out_base: str | None = None) -> dict:
    """Source a part. Tries the installed KiCad libraries first; if absent and
    ``do_pull`` is set, pulls by MPN via easyeda2kicad.

    Returns a dict with ``source`` in {"local", "easyeda2kicad", "not_found"}.
    """
    symbols = local.find_symbol(query)
    if symbols:
        return {
            "query": query,
            "source": "local",
            "symbols": symbols,
            "footprints": local.find_footprint(query),
        }
    if do_pull:
        res = pull.pull_mpn(query, out_base or query)
        res.update({"query": query, "source": "easyeda2kicad"})
        return res
    return {
        "query": query,
        "source": "not_found",
        "symbols": [],
        "suggestion": (
            f"'{query}' is not in the installed KiCad libraries. If it's a manufacturer "
            f"part number, pull it online: pull-part {query}"
        ),
    }
