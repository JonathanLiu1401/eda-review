# kicad-review v1 Part Sourcing — Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Let Claude find/pull a part's KiCad **symbol + footprint (+ 3D)** natively, so the user never searches online — a tiered chain: (1) the **installed KiCad libraries** (free, offline, highest-trust — ~6/9 of a typical board's parts are already there), then (2) an **`easyeda2kicad` online pull** by MPN (keyless, verified end-to-end). AI-from-datasheet is a documented draft-only fallback, not built here.

**Architecture:** A new `kicad_mcp/parts/` package. `local.py` greps the installed `*.kicad_sym` / `*.pretty` libs (located relative to the detected `kicad-cli`). `pull.py` resolves an MPN→LCSC via the keyless `jlcsearch` JSON API (stdlib urllib + browser UA), then shells out to the `easyeda2kicad` CLI (AGPL — invoked as a subprocess, never imported, so no copyleft on this plugin) to emit `.kicad_sym`+`.kicad_mod`+STEP/WRL. `source.py` is the fallback chain. Surfaces: CLI `find-symbol` / `pull-part`, MCP `kicad_find_symbol` / `kicad_pull_part`, a skill section.

**Tech Stack:** Python 3.10+, stdlib `urllib`/`subprocess`/`shutil`, the existing `kicad.find_kicad_cli`. `easyeda2kicad` is an optional external CLI (`pip install easyeda2kicad`), detected like `kicad-cli`. **NOT** added to pyproject deps (AGPL — kept as a detected subprocess tool).

**NOT in this plan:** inserting a sourced symbol into a `.kicad_sch` (place-symbol) — that needs UUID/lib_symbols-cache scaffolding and runs into the geometric-connectivity wall; a separate increment. This plan stops at *acquiring* the KiCad files.

---

### Task 1: `local.py` — search installed KiCad libraries

**Files:** Create `kicad_mcp/parts/__init__.py`, `kicad_mcp/parts/local.py`; Test `tests/test_review_parts.py`.

- [ ] Golden test (gated on a KiCad install): `find_symbol("LM358")` returns ≥1 hit like `Amplifier_Operational:LM358`; `find_symbol("nonexistent_xyz")` returns `[]`. `symbol_dirs()` returns an existing dir under the KiCad share tree.
- [ ] Implement `symbol_dirs()` / `footprint_dirs()`: from `kicad.find_kicad_cli()`, walk to the install root (parent of `bin`/`MacOS`) and return `share/kicad/symbols` & `.../footprints` candidates that exist (Windows `<root>\share\kicad`, Linux `/usr/share/kicad`, macOS `.../SharedSupport`). Implement `find_symbol(query) -> list[str]` scanning each `*.kicad_sym` for `(symbol "<query>"` or `(symbol "<query>_…"` (case-insensitive), returning `"LibStem:SymbolName"`. Same shape `find_footprint(query)` over `*.pretty/*.kicad_mod` filenames.
- [ ] Run → PASS. Commit.

### Task 2: `pull.py` — MPN → LCSC → easyeda2kicad

**Files:** Create `kicad_mcp/parts/pull.py`; extend `tests/test_review_parts.py`.

- [ ] Test (pure): `resolve_lcsc` parses a stubbed jlcsearch JSON (`{"components":[{"lcsc":22397078,"mfr":"DRV8234RTER",...}]}`) → `"C22397078"`. Test `have_easyeda2kicad()` returns a bool. (Network/tool-dependent end-to-end pull is gated behind `KICAD_REVIEW_NETWORK_TESTS=1`.)
- [ ] Implement `resolve_lcsc(mpn) -> str | None` (urllib GET `https://jlcsearch.tscircuit.com/components/list.json?search=<mpn>` with a browser `User-Agent`; take the first component's `lcsc`, format `C{n}`). `have_easyeda2kicad()` (`shutil.which` or `py -m easyeda2kicad --help` probe). `pull_lcsc(lcsc_id, out_base)` → subprocess `[sys.executable, "-m", "easyeda2kicad", "--full", f"--lcsc_id={lcsc_id}", "--output", out_base, "--overwrite"]`; return `{symbol, footprint_dir, model_dir}` paths; raise `PartSourceError` on failure. `pull_mpn(mpn, out_base)` resolves then pulls.
- [ ] Run → PASS. Commit.

### Task 3: `source.py` — the fallback chain

**Files:** Create `kicad_mcp/parts/source.py`; extend test.

- [ ] Test: `find_part("LM358")` (gated KiCad) reports `source=="local"` with the lib_id; with a query not in local libs + `pull=False`, reports `source=="not_found"` and a suggestion to pull.
- [ ] Implement `find_part(query, pull=False, out_base=None) -> dict`: try `local.find_symbol`; if hits → `{source:"local", symbols:[...], footprints:[...]}`. Else if `pull` and it looks like an MPN → `pull.pull_mpn` → `{source:"easyeda2kicad", ...paths}`. Else `{source:"not_found", suggestion:"…try pull-part <MPN>…"}`.
- [ ] Run → PASS. Commit.

### Task 4: surfaces — CLI + MCP + skill

**Files:** Modify `lib/kicad_review_cli.py`, `kicad_mcp/tools/review_tools.py`, `skills/kicad-design/SKILL.md`, `.github/workflows/ci.yml` (add `kicad_mcp/parts` to lint + cov); extend test.

- [ ] CLI `find-symbol <query>` (prints local lib_id hits) and `pull-part <MPN> [--out DIR]` (resolves + pulls, prints generated file paths). MCP `kicad_find_symbol(query)` and `kicad_pull_part(mpn, out_dir=None)` (behind `_safe`). SKILL.md: a "Sourcing parts" section — try local first, pull by MPN if absent, and that pulled parts are draft-verify-against-datasheet.
- [ ] ci.yml: add `kicad_mcp/parts` to the ruff scope and `--cov`. Run the full suite + ruff; commit; push; watch CI.

---

## Self-Review
- **Spec coverage:** tiers (1) local + (2) easyeda2kicad pull from the approved part-sourcing design; AI-draft + place-symbol explicitly deferred. ✓
- **Placeholder scan:** each task has a concrete test + implementation. Network pull is opt-in (`KICAD_REVIEW_NETWORK_TESTS`) so CI stays hermetic. ✓
- **Licensing:** easyeda2kicad (AGPL) invoked as a subprocess only, not a dependency. ✓
