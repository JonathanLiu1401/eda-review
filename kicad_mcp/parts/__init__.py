"""kicad_mcp.parts -- source a part's KiCad symbol/footprint without manual searching.

Tiered: (1) the installed KiCad libraries (offline, highest-trust), then (2) an
``easyeda2kicad`` online pull by manufacturer part number (keyless). AI-from-datasheet
is a documented draft-only fallback and is not built here.
"""

from .source import PartSourceError, find_part  # noqa: F401

__all__ = ["find_part", "PartSourceError"]
