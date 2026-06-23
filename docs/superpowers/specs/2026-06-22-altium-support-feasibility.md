# Altium Designer Support — Feasibility Spec & Phased Plan

- **Date:** 2026-06-22
- **Status:** Approved & in progress — the EDA-neutral core (`eda_core/`), the Altium backend
  (`altium_review/`), the `altium-design` skill, and the Phase-0 setup/spike doc
  (`docs/altium_eda_agent_setup.md`) are built and CI-green. Phases 2–4 (live BOM/DFM wiring,
  guarded edits) are gated on the Phase-0 spike returning one real eda-agent capture on Altium
  26.7.1.11 (the one step that needs the running Altium GUI and cannot be done headless).
- **Author:** kicad-review maintainer (Claude)
- **Scope:** Bringing kicad-review's "review-first, guarded-edit" capabilities to Altium Designer.
- **Decision context (from brainstorming):** User has a **full licensed Altium install**, this is for
  **internal use only** (no distribution), and **all four capability areas are in scope**:
  read/review/audit, parts & BOM stock, JLCPCB DFM/stackup, and guarded edits.

---

## 1. Goal

Let Claude natively read, review, and make *guarded* edits to Altium Designer designs
(`.PcbDoc` / `.SchDoc` / `.PrjPcb`) with the same philosophy that drives kicad-review:
**LLMs are good at reviewing, bad at building**, so the tool reviews deeply, sources parts,
checks manufacturability against authoritative data, and only performs deterministic,
fully-specified edits behind a validation guard — never freehand routing or placement.

## 2. The core finding (why this is not a 1:1 port)

kicad-review's guarded edits are safe because KiCad gives us two things **Altium does not**:

| Property kicad-review relies on | KiCad | Altium |
|---|---|---|
| **Text design files** (surgical byte-span/UUID edits, diffs, atomic `os.replace`, round-trip parse) | ✅ S-expressions | ❌ Binary OLE/CFB compound documents |
| **Free headless validator** (`kicad-cli` runs real ERC/DRC to gate every edit) | ✅ `kicad-cli` | ❌ No official headless CLI — DRC/ERC/Gerber run *inside the GUI* |

**Consequence:** our surgical-text-edit machinery (`edit/zones.py`, `edit/surgical.py`,
`edit/board_rules.py`, `edit/board_stackup.py`) and our `kicad-cli` guard do **not** transfer.
The *substrate that makes guarded edits safe* is absent on the headless path.

**What rescues edits:** the **live-GUI bridge**. Because Altium scripting (DelphiScript) runs
inside a *running* Altium session, an edit can be made through Altium's own object API and then
validated by running Altium's *real* DRC. The guard reforms with different mechanics:
`backup → API mutation → run real Altium DRC → diff/report → user approves (or Altium-undo)`.
So with a licensed install, **review/audit ports well and guarded edits become feasible** —
just via the API + live DRC, not surgical text editing.

## 3. Landscape — tools that already exist (researched 2026-06-22)

| Tool | What it is | License | Maturity | Role here |
|---|---|---|---|---|
| **[eda-agent](https://github.com/salitronic/eda-agent)** | MCP server bridging AI ↔ *live* Altium via a persistent DelphiScript IPC loop. **300+ tools**: project/BOM/connectivity, schematic ERC, PCB DRC/layer-stack/analysis, library, and a **`design_lint_report`** (31 audit checks) + **`design_review_snapshot`** + web dashboard. | **Apache-2.0** (clean for internal use *and* future distribution) | 63★, 97 commits, **experimental**, single-threaded, no releases | **Chosen backend** (run as a separate MCP server) |
| **[altium_monkey](https://github.com/wavenumber-eng/altium_monkey)** | Pure-Python read/write/render of `.PcbDoc`/`.SchDoc`/`.PcbLib`/`.SchLib`/`.PrjPcb`/`.OutJob` **without Altium running**. SVG render; extract nets/BOM/rules/layers. | **AGPL-3.0** (fine internally; would force whole-plugin AGPL if ever distributed) | 148★, active (release 2026-06-22) | Future headless/CI read path (Approach 3) |
| python-altium / python-schdoc / PyAltium / Altium-Schematic-Parser | Partial `.SchDoc` parsers / SVG / JSON exporters | Mixed | Varies, narrower | Reference only |
| **KiCad 8/9 native Altium importer** | Imports `.PcbDoc`/`.SchDoc`/`.PrjPcb` into KiCad | Free | Good geometry fidelity | **Rejected for review:** design rules (trace width, clearance, via) **do not import** — that's exactly the JLCPCB DFM data we'd review. Lossy where it matters most. |

## 4. Chosen architecture

**Approach 1 — eda-agent as a separate MCP server, with a backend-agnostic review layer on top.**

```
        ┌─────────────────────────────────────────────────────────┐
        │  altium-design skill  (review methodology + boundary)    │
        │  drives the tools below; synthesizes the review          │
        └───────────────┬─────────────────────────┬───────────────┘
                        │                          │
         (queries/edits)│                          │ (board facts → checks)
                        ▼                          ▼
        ┌───────────────────────────┐   ┌──────────────────────────────┐
        │  eda-agent MCP server      │   │  shared review/value-add layer│
        │  (separate process,        │   │  (NEW, backend-agnostic)      │
        │   Apache-2.0, unmodified)  │   │  - parts/stock.py  (REUSED)   │
        │  ──────────────────────    │   │  - JLCPCB reference data      │
        │  live DelphiScript bridge  │   │    (REUSED from review/jlcpcb)│
        │  into running Altium       │   │  - DFM/stackup compare        │
        └───────────────┬───────────┘   │  - consumes a normalized IR   │
                        │                └──────────────┬───────────────┘
                        ▼                               │
                 Running Altium GUI                     │
                 (real ERC/DRC,                         │
                  object API)        eda-agent output ──┘ via an IR adapter
```

**Key design move — a normalized "board facts" IR.** Our DFM/parts/review logic should consume a
small **EDA-neutral intermediate representation** (BOM = list of MPNs+refs; design rules =
clearance/track/via/drill; stackup = ordered dielectric/copper layers; geometry = measured
track/via/annular minimums; board outline present?), produced by a thin **adapter** that maps
eda-agent's query output → the IR. This:
- makes the **parts** and **JLCPCB reference** reuse *real* (shared code, not copy-paste),
- keeps the value-add layer independent of Altium internals,
- and leaves a clean seam to later add an **altium_monkey** adapter (headless/CI) or even unify
  with kicad-review behind the same IR (Approach 3) — without rewriting the checks.

Our tools never talk to Altium directly: the skill calls eda-agent to **extract** board facts,
the adapter normalizes them to the IR, and our reused checks run on the IR.

## 5. What ports cleanly vs. what is rebuilt

| kicad-review module | Reuse verdict | Altium plan |
|---|---|---|
| `parts/stock.py` (DigiKey + JLCPCB validity/stock) | ✅ **100% reuse** — format-agnostic, only needs MPNs | Unchanged; fed by IR BOM |
| `review/jlcpcb.py` reference data (`SOURCES`, `VERIFIED`, `capabilities_for`, `STACKUPS`, `reference_stackup`) | ✅ **Reuse as data** | Move to shared module; consumed by DFM compare |
| `review/jlcpcb.py` board-reading (`parse_board_stackup`, `board_rules`, geometry measurement) | ❌ KiCad-text-specific | Rebuild as IR adapter over eda-agent queries |
| `parts/bom.py` `extract_parts` (KiCad sch walk) | ❌ KiCad-sch-specific | Rebuild via eda-agent BOM/connectivity tools → IR |
| `review/fab.py` (gerber/drill/pos/step via `kicad-cli`) | ❌ `kicad-cli`-specific | Altium has its own Gerber/OutJob export; drive via eda-agent (or GUI) |
| `edit/zones.py`, `edit/surgical.py`, `edit/board_rules.py`, `edit/board_stackup.py` | ❌ surgical-text | Rebuild as eda-agent API mutations behind the same guard *contract* |
| Review methodology / work-checker hardware critic | ✅ **Reuse** | Same prioritized-findings synthesis |

## 6. Guard design for edits (Phase 4)

Mirrors kicad-review's "copy → validate → diff → approve → atomic" guard, re-expressed for the
live bridge and the DRC-delta pattern we already use for ERC/DRC:

1. **Pre-flight:** confirm the target doc is open in Altium; take a **backup** (file copy of the
   `.PcbDoc` + note Altium local-history point).
2. **Baseline:** run eda-agent DRC, record the existing violation set.
3. **Mutate:** call eda-agent `obj_create`/`obj_modify` for a **deterministic, fully-specified**
   change only (e.g., a polygon copper pour: explicit layer + net-by-name + outline points).
4. **Validate:** re-run eda-agent DRC; compute the **delta**. Only *new* violations block
   (same rule kicad-review uses — a pre-existing violation is not the edit's fault).
5. **Report & approve:** show the object-level diff + DRC delta; user approves in-GUI, or we
   **revert** via Altium undo / backup restore.

**The boundary (unchanged philosophy):** only deterministic pours/planes (polygon + net + layer).
**No autoroute / autoplace** even though eda-agent exposes them — routing and component placement
stay **advice-only**. This is enforced in the skill, not left to the model's discretion.

## 7. Phased plan (risk-ordered — same order kicad-review was built)

- **Phase 0 — Spike (gate the whole project).** Install eda-agent; confirm its DelphiScript bridge
  connects to *the user's specific Altium version*; run **read-only** `design_review_snapshot` +
  `design_lint_report` + BOM extract on a real Trellis Altium board. **Gate:** does it actually
  work on this setup and version? Everything downstream assumes yes.
- **Phase 1 — Read / review / audit.** `altium-design` skill + IR adapter over eda-agent queries
  (project/components/nets/rules/geometry) → prioritized structured review (reuse review synthesis
  + work-checker hardware critic). Read-only; lowest risk.
- **Phase 2 — Parts & BOM stock.** eda-agent BOM → IR MPN list → **reuse `parts/stock.py`
  unchanged** → in-stock/validity report (valid if on DigiKey **or** JLCPCB).
- **Phase 3 — JLCPCB DFM / stackup.** Map eda-agent rules + stackup + measured geometry → IR →
  **reuse JLCPCB reference data** → DFM report (blockers vs. major), stackup compare. Pull only
  from authoritative JLCPCB sources, never hallucinated.
- **Phase 4 — Guarded basic-pour edits.** `add-pour` via eda-agent `obj_create` behind the
  §6 guard; boundary enforcement (routing/placement refused with advice). Highest risk, done last.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| eda-agent is **experimental**, single-threaded; can crash DelphiScript or freeze Altium's own buttons | Queries before mutations; **Phase 0 gate**; pin a known-good commit; keep our layer decoupled via the IR adapter so the backend is swappable |
| **No headless/CI** (GUI must be running) | Accepted for internal manual-review use now; altium_monkey can later add a headless read adapter (the IR seam makes this drop-in) |
| Binary format → **no text diff** | Diff at the *object* level (what eda-agent reports) + rely on Altium backup + **DRC delta** as the guard, not file diff |
| **ECO (sch→PCB sync) unreliable** in eda-agent | Keep guarded edits **PCB-local** (pours); do not attempt automated schematic→board sync edits |
| **Altium API version drift** (docs lag; interfaces changed across versions) | Test against the user's exact Altium version in Phase 0; treat any tool that fails there as out of scope |
| Altium DRC ground-truth depends on the board's configured rules being correct | Run our **JLCPCB DFM compare independent of Altium's configured rules** — that's the value-add, and it catches under-configured rule sets |
| Dependency on a third-party project for core function | Apache-2.0 lets us vendor/fork if upstream stalls; the IR adapter means we own the seam |

## 9. Open questions (decide before/within writing-plans)

1. **Repo layout:** same repo as a parallel `altium_review/` package sharing a `shared/`
   core (parts + JLCPCB reference + IR), or a sibling repo? *Recommendation: same repo, shared core* —
   maximizes reuse and keeps one test/CI pipeline.
2. **eda-agent lifecycle:** how does the skill detect / prompt the user to start the in-Altium
   polling loop (it needs `File > Run Script > StartMCPServer`), and handle its 10-min idle shutdown?
3. **Target Altium version(s):** which exact version is installed? Drives Phase 0.
4. **Vendoring:** pin eda-agent to a tested commit, or track upstream?

## 10. Success criteria

- Each phase verified on a **real Trellis Altium board** (not a toy).
- Review depth on par with kicad-review (prioritized findings, manufacturability, sourcing).
- Guarded edits **never** leave the board with *new* DRC violations vs. baseline; routing/placement
  requests are **refused with advice**, never executed.
- Parts validity uses the same "valid if in stock on DigiKey **or** JLCPCB" rule as kicad-review.

## 11. Sources

- eda-agent — https://github.com/salitronic/eda-agent
- altium_monkey — https://github.com/wavenumber-eng/altium_monkey
- python-altium (format docs) — https://github.com/vadmium/python-altium/blob/master/format.md
- PyAltium — https://pypi.org/project/PyAltium/
- Altium scripting / DelphiScript / PCB API — https://www.altium.com/documentation/altium-designer/scripting
- KiCad Altium importer (rules-not-imported caveat) — https://www.nextpcb.com/blog/how-to-convert-altium-to-kicad-vice-versa
