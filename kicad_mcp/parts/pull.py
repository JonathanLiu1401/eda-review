"""Pull a KiCad symbol+footprint+3D for a part by MPN, online and keyless.

MPN -> LCSC part number (the keyless ``jlcsearch`` JSON API) -> ``easyeda2kicad`` (the
AGPL CLI, invoked as a *subprocess* so it imposes no copyleft on this plugin).
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess  # nosec B404 - invoked only with constructed arg lists, never a shell
import sys
import urllib.parse
import urllib.request

from eda_core.errors import PartSourceError  # noqa: F401  (canonical definition moved to eda_core)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_JLCSEARCH = "https://jlcsearch.tscircuit.com/components/list.json?search="


def parse_lcsc_response(data: dict) -> str | None:
    """Pure: extract the first ``C<n>`` LCSC id from a jlcsearch JSON payload."""
    comps = data.get("components") or []
    if not comps:
        return None
    lcsc = comps[0].get("lcsc")
    return f"C{lcsc}" if lcsc else None


def resolve_lcsc(mpn: str, timeout: int = 15) -> str | None:
    """Resolve a manufacturer part number to an LCSC ``C<n>`` id (or None)."""
    url = _JLCSEARCH + urllib.parse.quote(mpn)
    req = urllib.request.Request(url, headers=_UA)  # noqa: S310 - https only, fixed host
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        data = json.loads(r.read().decode("utf-8"))
    return parse_lcsc_response(data)


def have_easyeda2kicad() -> bool:
    try:
        r = subprocess.run(  # nosec B603
            [sys.executable, "-m", "easyeda2kicad", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # exit 0 only -- a "No module named easyeda2kicad" error also contains the name
    return r.returncode == 0


def pull_lcsc(lcsc_id: str, out_base: str, timeout: int = 180) -> dict:
    """Pull symbol+footprint+3D for an LCSC id via easyeda2kicad. ``out_base`` is a path
    prefix; easyeda2kicad writes ``<out_base>.kicad_sym`` / ``.pretty`` / ``.3dshapes``."""
    if not have_easyeda2kicad():
        raise PartSourceError("easyeda2kicad not installed -- `pip install easyeda2kicad`")
    out_base = str(out_base)
    r = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "easyeda2kicad",
            "--full",
            f"--lcsc_id={lcsc_id}",
            "--output",
            out_base,
            "--overwrite",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    sym = Path(out_base + ".kicad_sym")
    if r.returncode != 0 or not sym.is_file():
        raise PartSourceError(
            f"easyeda2kicad failed for {lcsc_id}: {(r.stderr or r.stdout).strip()[:200]}"
        )
    return {
        "lcsc": lcsc_id,
        "symbol": str(sym),
        "footprint_dir": str(Path(out_base + ".pretty")),
        "model_dir": str(Path(out_base + ".3dshapes")),
    }


def pull_mpn(mpn: str, out_base: str, timeout: int = 180) -> dict:
    lcsc = resolve_lcsc(mpn)
    if not lcsc:
        raise PartSourceError(f"no LCSC part found for MPN {mpn!r}")
    res = pull_lcsc(lcsc, out_base, timeout)
    res["mpn"] = mpn
    return res
