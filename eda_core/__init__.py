"""EDA-neutral shared core for the review tooling.

This package holds the format-agnostic logic that BOTH the KiCad backend (``kicad_mcp``)
and the Altium backend (``altium_review``) build on:

* :mod:`eda_core.stock` -- distributor availability (DigiKey + JLCPCB), needs only an MPN.
* :mod:`eda_core.parts` -- a BOM sourcing sweep over a list of parts (distributor check injected).
* :mod:`eda_core.jlcpcb` -- JLCPCB's authoritative capability data + ``grade_jlcpcb(facts)``.
* :mod:`eda_core.ir` -- the EDA-neutral intermediate representation (``BoardFacts``, ``BomPart``).

Dependency rule: **the core never imports from a backend.** Backends build the IR from their
own file/API access and call into here; anything backend-specific (a distributor checker, a
geometry reader) is passed in as data or as a callable.
"""
