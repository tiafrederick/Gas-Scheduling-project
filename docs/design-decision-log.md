# Design Decision Log (DDL)

Living record of architectural decisions. Status: Proposed → Accepted → (Superseded).
Challenge any of these; that's the point.

| # | Decision | Status | Rationale / notes |
|---|---|---|---|
| DDL-001 | Canonical system-of-record = **bitemporal relational** model; knowledge graph is a **derived** read-model | Accepted | Graphs are weak at versioned/temporal/auditable facts; relational core + graph projection gets both. |
| DDL-002 | Keys: companies by **FERC CID**; points by composite **`(TSP FERC CID, LOC)`**; interconnects resolved from `Up/Dn FERC CID` + `Up/Dn Loc` | Accepted | Proven in the interconnect spike (round-trip 83004↔4208). |
| DDL-003 | Notices = **immutable events** w/ lifecycle (Initiate/Update/Complete) + supersession; header parsed deterministically, body extracted to **typed facts** w/ provenance + confidence + source span | Accepted | Anti-hallucination + bitemporal auditability. |
| DDL-004 | LLM behind a **provider-agnostic** interface; **extraction ≠ reasoning**; no coupling to Fable 5 | Accepted | Model choice will change; different jobs, different models. Fable 5 = reasoning + (later) UI iteration. |
| DDL-005 | **No UI** in Phase 0/1; thinking workspace = canonical store + notebooks/CLI | Accepted (user) | Avoid encoding an immature mental model into software. |
| DDL-006 | **Hybrid intake**: manual export drops now; adapter interfaces designed but automation OFF | Accepted (user) | ToS-safe + reliable for v1; automation later per-portal. |
| DDL-007 | **Personal/local, public data only**; cloud LLM permitted; positions/nominations excluded | Accepted (user) | Index-of-Customers holdings are public FERC postings. |
| DDL-008 | v1 = **cross-pipeline impact analysis** (AlexSEG → SESH deliveries) | Accepted (user) | Hardest, highest-value slice; proves the thesis. |
| DDL-009 | **Portfolio commercial reachability** (CGT+SESH+Sabine + direct interconnects, direction flag, no hydraulics) | Accepted (user) | Enough for impact/alternate-path; avoids over-modeling. |
| DDL-010 | **No graph database**; reachability via recursive SQL / in-memory `networkx` | Accepted | Proven: `nge/reach.py` answers the v1 impact question with a recursive CTE over `interconnect`. |
| DDL-011 | Stack: **Python + DuckDB + Pydantic** (pdfplumber/networkx as optional extras) | Accepted | DuckDB = zero-ops single-file analytical SQL (recursive CTEs, native CSV/Parquet), portable to Postgres. Core resolution logic + tests stay stdlib-only. |
| DDL-012 | **Acquisition v2**: automated fetch of **public** EBB pages, gated on the environment's network policy; one Enbridge InfoPost adapter serves **SESH + Egan + Bobcat**, one gasnom adapter serves Sabine. **Confidential shipper-login data (BP storage balances, scheduled quantities) stays manual-drop only; BP credentials never enter the container.** Low cadence (daily/per-cycle); fetched files land in the raw zone with the same provenance as manual drops. | Accepted (user) | Verified 2026-07-04: Egan (`EGHome.asp?Pipe=EG`) and Bobcat (`BGSHome.asp?Pipe=BGS`) post on Enbridge InfoPost — same platform as SESH. All egress from this environment is currently blocked at the proxy (even example.com), so fetching activates only after the user allowlists `infopost.enbridge.com` + `www.gasnom.com` in the environment's network settings. No MCP connector/skill needed (registry checked — nothing relevant exists); built-in fetch + preinstalled Playwright suffice. |

## Open items
- **Egan/Bobcat FERC CIDs** — `pipeline` rows carry loud `PENDING-EG` / `PENDING-BGS`
  placeholder keys; backfill from the FERC CID listing or the portals' own postings in the
  next (egress-enabled) session, per `docs/next-session.md` Step 2.
- **Session restart for egress** — user set the domain allowlist to "All domains"
  (2026-07-04), but egress policy binds at container start; the live-fetch work executes
  in a **fresh session**. Handoff: `docs/next-session.md`.
- Storage balance fact shape — design against the first real EG/BGS storage posting
  (public "storage conditions") + manual-drop balance exports; do not assume format.
- AlexSEG (and other CGT segments) → point mapping — needs CGT location data parse.
- `operational_capacity_fact` columns — finalize against a real OAC/OA_MLC table.
- LLM extraction step — the eval harness + regex baseline now exist
  (`src/nge/extract/`); the LLM extractor implements the same interface and must beat the
  baseline on the growing gold set. Needs `ANTHROPIC_API_KEY` in the environment.
