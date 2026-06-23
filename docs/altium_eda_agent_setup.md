# Altium backend setup + Phase-0 spike (eda-agent on Altium 26.7.1.11)

The Altium backend drives the third-party **eda-agent** MCP server, a DelphiScript bridge into a
*running* Altium session. This is one-time setup, then a **spike** that must pass before any
Altium review output is trusted. Background: `docs/superpowers/specs/2026-06-22-altium-support-feasibility.md`.

> eda-agent is Apache-2.0, experimental (no releases), and documents "AD20+ preferred". Altium
> 26.7.1.11 is newer than its tested range — that is exactly why the spike captures the *real*
> tool output before we rely on it.

## 1. Install eda-agent

```bash
pipx install eda-agent        # or: pip install eda-agent  (Windows, Python 3.11+)
eda-agent install-scripts     # writes the DelphiScript project to %USERPROFILE%\EDA Agent\scripts\
```

## 2. Register + start the bridge inside Altium

1. Altium: **DXP → Preferences → Scripting System → Global Projects → Install from file** →
   select `Altium_API.PrjScr` (from the path above).
2. Open the design you want to review (`.PrjPcb` with its `.PcbDoc`/`.SchDoc`).
3. **File → Run Script… →** expand `Altium_API → Dispatcher.pas` → select **StartMCPServer** →
   **Run**. The in-Altium status window should show the polling loop running.
   - It single-threads Altium's scripting engine: Altium's own script-backed buttons may sit
     unresponsive while the loop runs. Click **Detach** (or `app_detach`) to release it.
   - It auto-shuts-down after ~10 min idle; Claude Code pings to keep it alive during a session.

## 3. Connect Claude Code

```bash
claude mcp add altium eda-agent          # or: claude mcp add altium /full/path/to/eda-agent.exe
```

Verify: `/mcp` in the session lists **altium** as connected. If it doesn't, the loop isn't
running (redo step 2) or the path is wrong.

## 4. The Phase-0 spike (DO THIS BEFORE TRUSTING ANY REVIEW)

The spike is read-only and proves the bridge works on *this* Altium and that our adapter's
assumptions match reality. Run each and **save the raw JSON**:

| eda-agent tool | What we need from it | Confirm |
|---|---|---|
| `proj_get_stats`, `pcb_get_board_info` | board exists, layer count, dimensions | bridge is alive; what key holds **copper layer count** + **board thickness** + its **unit** |
| `design_review_snapshot` | the 8–12 bundled reads succeed | no DelphiScript crash on a real board |
| `design_lint_report` | 31 audit checks return | shape `{checked, violations:[{audit,severity,count,items}]}` |
| `proj_get_bom` | BOM rows with `mpn` | shape matches `altium_review.adapter.bom_to_parts` (designator/mpn/value) |
| `pcb_get_design_rules` (+ `pcb_get_rule_properties`) | configured min clearance/width/hole/via | **field names + unit** (mil?) for `rules_to_configured` |
| `pcb_get_layer_stackup` | dielectric/copper thicknesses + εr | **field names + unit** for `stackup_to_layers` |
| `pcb_run_drc` | live DRC violations | shape (`gap_mils`/`required_mils` confirm **mils**) — the edit-guard signal |

### Reconcile against the adapter

`altium_review/adapter.py` confidently maps the **BOM** and **DRC** shapes (documented by
eda-agent). It marks **`rules_to_configured`** and **`stackup_to_layers`** as *assumed* shapes.
With the spike JSON in hand:

1. If the real `pcb_get_design_rules` / `pcb_get_layer_stackup` field names or units differ from
   the assumed shapes, update those two functions (and the `unit` passed to `review_board`).
2. Add a fixture test in `tests/test_review_altium.py` using a **real** (trimmed) capture, so the
   mapping is pinned to reality, not to a guess.
3. Only then trust `review_board`'s `manufacturability` numbers. (A wrong unit silently turns a
   "manufacturable" verdict into garbage — this step is the safeguard.)

## 5. Then review

Follow the `altium-design` skill. Sourcing (DigiKey/JLCPCB) and the JLCPCB DFM grade are the
shared, EDA-neutral engine — identical rules to the KiCad backend. Routing/placement stays
advice-only; guarded basic-pour edits come only after this spike.
