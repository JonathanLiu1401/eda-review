"""Search the installed KiCad symbol/footprint libraries (offline, no network)."""

from __future__ import annotations

from pathlib import Path
import re

from kicad_mcp.review import kicad

# top-level library symbols are part names; ``Name_0_1`` etc. are unit/graphic sub-symbols
_SUBUNIT = re.compile(r"_\d+_\d+$")
_SYMBOL = re.compile(r'\(symbol "([^"]+)"')


def _share_roots() -> list[Path]:
    """Candidate ``share/kicad`` roots, derived from the detected kicad-cli."""
    roots: list[Path] = []
    try:
        cli = Path(kicad.find_kicad_cli())
        root = cli.parent.parent  # <root>/bin/kicad-cli -> <root>
        roots.append(root / "share" / "kicad")  # Windows / Linux install
        roots.append(cli.parent.parent / "SharedSupport")  # macOS app bundle
    except kicad.KiCadError:
        pass
    roots.append(Path("/usr/share/kicad"))  # Linux system package
    roots.append(Path("/usr/local/share/kicad"))
    return roots


def symbol_dirs() -> list[Path]:
    return [r / "symbols" for r in _share_roots() if (r / "symbols").is_dir()]


def footprint_dirs() -> list[Path]:
    return [r / "footprints" for r in _share_roots() if (r / "footprints").is_dir()]


def find_symbol(query: str, limit: int = 40) -> list[str]:
    """Return ``LibStem:SymbolName`` ids whose symbol name contains ``query`` (case
    insensitive), across the installed ``*.kicad_sym`` libraries."""
    q = query.strip().lower()
    if not q:
        return []
    hits: list[str] = []
    for d in symbol_dirs():
        for f in sorted(d.glob("*.kicad_sym")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _SYMBOL.finditer(text):
                name = m.group(1)
                if _SUBUNIT.search(name):
                    continue  # a unit/graphic sub-symbol, not a part
                if q in name.lower():
                    hits.append(f"{f.stem}:{name}")
                    if len(hits) >= limit:
                        return hits
    return hits


def find_footprint(query: str, limit: int = 40) -> list[str]:
    """Return ``LibStem:Footprint`` ids whose footprint filename contains ``query``."""
    q = query.strip().lower()
    if not q:
        return []
    hits: list[str] = []
    for d in footprint_dirs():
        for lib in sorted(d.glob("*.pretty")):
            for mod in sorted(lib.glob("*.kicad_mod")):
                if q in mod.stem.lower():
                    hits.append(f"{lib.stem}:{mod.stem}")
                    if len(hits) >= limit:
                        return hits
    return hits
