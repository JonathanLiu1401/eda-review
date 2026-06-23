---
name: altium-design
description: Use when reading, understanding, or reviewing Altium Designer designs — any .PcbDoc / .SchDoc / .PrjPcb, or tasks involving Altium schematic/PCB review, DRC/ERC, BOM sourcing, JLCPCB DFM/stackup, or "review my Altium board". Review-first; drives the eda-agent MCP bridge into a running Altium session. Routing/placement is advice-only.
---

# Altium Design Review

Read and **review** Altium Designer designs, source the BOM, and grade manufacturability
against JLCPCB — the same review-first philosophy as the KiCad path, on a different substrate.

**Why review-first here is not a choice but a property of the tool.** Altium files are binary
(OLE/CFB) and there is no free headless validator, so the surgical-text edits and `kicad-cli`
guard the KiCad backend relies on do **not** exist. What *does* exist is the live-GUI bridge:
[eda-agent](https://github.com/salitronic/eda-agent) runs DelphiScript inside a running Altium
session, so we can read everything and (later, carefully) make guarded edits validated by
Altium's *real* DRC. See `docs/superpowers/specs/2026-06-22-altium-support-feasibility.md`.

## When to activate

- Any `.PcbDoc`, `.SchDoc`, `.PcbLib`, `.SchLib`, or `.PrjPcb` is mentioned or present.
- The user says Altium, Altium Designer, or asks to review an Altium board/schematic, check
  its BOM stock, or check JLCPCB manufacturability of an Altium design.

## The boundary (same philosophy as kicad-design — enforced here)

LLMs are good at reviewing, bad at building. So:

- **Read / review / audit:** yes — the core value.
- **Parts & BOM stock, JLCPCB DFM/stackup:** yes — shared, EDA-neutral checks.
- **Routing and component placement: ADVICE ONLY. Never execute it.** eda-agent *exposes*
  `pcb_move_components` and autoroute-style tools — **do not call them.** If asked to route or
  place, explain the boundary and give specific guidance (what net, what width, what keep-out)
  for the human to do in Altium.
- **Deterministic, fully-specified edits (e.g. a copper pour: explicit layer + net + outline):**
  allowed **only behind the guard** (§ Guarded edits) — and only after the Phase-0 spike.

## Prerequisite: the eda-agent bridge (Phase-0 spike comes first)

This backend cannot work until eda-agent is installed and its polling loop is running inside
Altium. **Before any review, do the one-time setup and the spike** in
`docs/altium_eda_agent_setup.md`. The spike is non-negotiable because eda-agent is experimental
and the user's Altium is 26.7.1.11 (newer than eda-agent's tested range) — the spike captures
the *real* JSON shapes/units and confirms the adapter assumptions below.

Quick check that the bridge is live: in the Claude Code session, `/mcp` should list `altium`
as connected. If not, the loop isn't running — send the user to the setup doc.

## Review workflow

1. **Confirm the bridge is live** (`/mcp` shows `altium`). If not, stop and point to setup.
2. **Read the design** via eda-agent's read tools (all by-name, all read-only):
   - `proj_get_stats`, `pcb_get_board_info` — counts, board size, layer count.
   - `design_review_snapshot` — bundles 8–12 reads (info, components, nets, rules, BOM…).
   - `design_lint_report` — 31 automated audit checks; triage its violations.
   - `proj_run_erc` / `pcb_run_drc` — Altium's own electrical/design-rule checks (ground truth).
3. **Source the BOM + grade JLCPCB DFM** with the shared engine. Gather the eda-agent JSON
   (`proj_get_bom`, `pcb_get_design_rules`, `pcb_get_layer_stackup`, `pcb_get_board_info`, plus
   the measured min-geometry from PCB-primitive queries), then run the pure pipeline:

   ```python
   from altium_review.review import review_board
   r = review_board(
       bom=<proj_get_bom JSON>,
       rules=<pcb_get_design_rules JSON>,
       stackup=<pcb_get_layer_stackup JSON>,
       board_info={"copper_layers": .., "copper_oz": .., "thickness": ..},   # thickness in `unit`
       geometry={"min_track_width": .., "min_via_drill": .., "min_annular_ring": ..},  # in `unit`
       unit="mil",   # Altium's length unit; the adapter converts everything to mm
   )
   # r = {sourcing: {parts, missing_mpn}, manufacturability: {findings, manufacturable, ...}, facts: {...}}
   ```

   - **Sourcing:** a part is `valid` if in stock on **DigiKey OR JLCPCB** (same rule as KiCad).
     `missing_mpn` lists components with no MPN/LCSC — an unsourced-part gap to surface.
   - **JLCPCB DFM:** `manufacturability.findings` are severity-tagged (blocker > major > minor),
     cited to JLCPCB's published capabilities — never hallucinate limits.
4. **Synthesize** a prioritized review (blockers first), citing eda-agent lint + DRC/ERC + the
   sourcing + DFM findings. Apply the work-checker hardware-review rigor.

## Guarded edits (basic pours only — after the spike, opt-in)

The guard reforms for the live bridge (no atomic text replace; use Altium + real DRC instead):

1. **Back up** the `.PcbDoc` (file copy) and note an Altium local-history point.
2. **Baseline:** `pcb_run_drc` → record the existing violation set.
3. **Mutate:** `obj_create` a *fully-specified* pour only (explicit copper layer + net-by-name +
   polygon outline). No autoroute, no autoplace.
4. **Validate:** `pcb_run_drc` again; compute the delta with `altium_review.adapter.drc_delta`.
   Only **new** violations block (a pre-existing one isn't the edit's fault).
5. **Report & approve:** show the object diff + DRC delta; the user approves in Altium, or you
   revert (Altium undo / restore the backup).

## Caveats (state these honestly)

- eda-agent is **experimental**, single-threaded: it can crash DelphiScript or freeze Altium's
  own buttons; queries are safer than mutations; it auto-shuts-down after 10 min idle.
- The `rules`/`stackup` JSON field names and the length **unit** are **assumed** until the
  Phase-0 spike confirms them on Altium 26.7.1.11. A wrong unit silently corrupts a
  "manufacturable" verdict — confirm before trusting DFM numbers.
- Keep edits PCB-local; eda-agent's schematic→PCB ECO sync is unreliable.
