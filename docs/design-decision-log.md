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
| DDL-012 | **Acquisition v2**: fetch **public** EBB pages via the Firecrawl MCP connector (a hosted scraper, not subject to this container's egress proxy) — no session restart required. One logical Enbridge InfoPost adapter serves **SESH + Egan + Bobcat**, one gasnom adapter serves Sabine. **Confidential shipper-login data (BP storage balances, scheduled quantities) stays manual-drop only; BP credentials never enter the container.** Fetched files land in `data/raw/<portal>/<date>/` with a `.meta.json` provenance sidecar (source, url, retrieved_at, sha256, content_type, bytes) — same discipline as manual drops. | Accepted (user) | **Superseded original finding**: direct curl/WebFetch egress from this container was blocked even after the user set the domain allowlist to "All domains" (policy binds at container boot). But the **Firecrawl MCP tool reaches these hosts today**, since it fetches from Firecrawl's own infrastructure. Landed 2026-07-04: Egan (`EgAllPoints.csv`, FERC CID **C000086**) and Bobcat (`BGSAllPoints.csv`, FERC CID **C001706**) point catalogs, both real, both on Enbridge InfoPost. `docs/next-session.md` (direct-fetch-in-a-fresh-session) is kept as a **fallback path** for a Firecrawl-free / no-credit-cost future run — not deleted. |

## Open items
- **CGT (TC eConnects) point catalog** — still only a 1-row fixture (SESH-83004
  reciprocal). Every Egan/Bobcat/Sabine↔CGT edge resolves at best to
  `resolved_cid_only` until CGT's real location data is landed. TC eConnects is an
  untested target for Firecrawl (different platform than InfoPost/gasnom) — try next.
- **Sabine Hub Services, L.L.C.** — confirmed via web search to be a *distinct* ONEOK
  legal entity from Sabine Pipe Line LLC (it administers Henry Hub volume tracking;
  Sabine Pipe Line provides the physical wheeling). gasnom's Sabine Pipe Line portal nav
  shows no separate Sabine Hub Services section/point-catalog — unresolved whether it
  has its own public EBB at all, or is purely an accounting/administrative function
  layered on Sabine Pipe Line's points. Needs a manual check (e.g. oneok.com) or your
  domain knowledge — do not guess a FERC CID for it.
- Storage balance fact shape — design against a real EG/BGS storage-conditions posting
  (public) + manual-drop balance exports; the InfoPost nav confirms Notices/Capacity/
  Index-of-Customers/Locations exist for Egan+Bobcat, but no storage-inventory-balance
  page was found in the public nav (consistent with DDL-007: balances are likely behind
  the LINK shipper login).
- AlexSEG (and other CGT segments) → point mapping — needs CGT location data parse.
- `operational_capacity_fact` columns — finalize against a real OAC/OA_MLC table
  (Egan/Bobcat capacity pages are on `rtba.enbridge.com`, a different subdomain/likely
  JS app — untested).
- LLM extraction step — the eval harness + regex baseline now exist
  (`src/nge/extract/`); the LLM extractor implements the same interface and must beat the
  baseline on the growing gold set. Needs `ANTHROPIC_API_KEY` in the environment.
