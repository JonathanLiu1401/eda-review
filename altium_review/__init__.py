"""Altium Designer backend for the review tooling.

Architecture (see docs/superpowers/specs/2026-06-22-altium-support-feasibility.md): the
``altium-design`` skill drives the third-party **eda-agent** MCP server (a DelphiScript bridge
into a *running* Altium session) to read the design, then feeds eda-agent's JSON into the PURE
transforms here. Those map Altium facts onto :mod:`eda_core`'s EDA-neutral IR, so the JLCPCB
grading and distributor sourcing are shared verbatim with the KiCad backend.

There is intentionally NO live-IPC Python here: the IPC is eda-agent's job, invoked by the skill.
This package only transforms and grades, which keeps it fully unit-testable without Altium.
"""
