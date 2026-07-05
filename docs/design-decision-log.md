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
| DDL-013 | **Segment→asset mapping** (`segment_asset_map` table): CGT's TC eConnects export carries a `Pipeline Seg Cd` per point (e.g. `ALEXDRIA`) that notices don't use directly (they say `AlexSEG`). The mapping is **inferred, not sourced** — confirmed by cross-referencing notice text ("Alexandria Compressor Station"/AlexSEG, "Banner Compressor Station"/BannSEG, "Grayson Compressor Station (StanSEG)") against each segment's point-catalog geographic footprint (county/town-name correspondence). Every row is confidence-tagged (0.7–0.9) with its evidence in `note`; segments with no notice evidence are left **unmapped**, not guessed. `nge.reach` uses this to walk asset → segment → its specific points → interconnects, so the AlexSEG report now cites `4208D`/`4208R` directly instead of "CGT the whole pipeline." | Accepted | CGT's location PDF has no `pdfplumber`/`pypdf`-readable path in this sandbox (`cryptography` binding is broken); parsed instead with PyMuPDF (`fitz`, no crypto dependency) + a custom anchored-vocabulary token parser (`src/nge/tools/parse_cgt_locations.py`) validated against 11 manually-verified rows spanning every structural edge case in the source. |

## CGT point catalog — landed (resolves prior open item)
153 physical points parsed from `CGT Location Data.pdf` into `data/samples/cgt_all_points.csv`
(marketer/shipper pooling points — `Loc` prefixed `P2/P3/P4`, `Loc Type Ind=PPT` — deliberately
excluded as TC eConnects accounting constructs, not physical interconnects; logged in the
raw-landing `.meta.json` sidecar for auditability). This **closed two new confidence-1.0
round-trips**: CGT `4123` ↔ Egan `45103`, and CGT `519` ↔ Sabine `11202`.

**Real-data finding, not a bug**: SESH `83004` (COLUMBIA GULF - DELHI) declares CGT loc `4208`
— CGT's own *undifferentiated* point, `loc_stat_ind='I'` (inactive) since 2022-08, superseded by
the split delivery/receipt pair `4208D`/`4208R`. SESH's posting hasn't been updated to reference
the split codes. The resolver correctly reports this as `resolved_cid_loc` (0.9), not a false
`resolved_roundtrip` (1.0) — a genuine cross-EBB staleness the system surfaces rather than hides.

## Open items
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
- `HouLn100`→HoumaSEG mapping (0.7 confidence) is geography-only (Terrebonne Parish =
  Houma, LA); unlike AlexSEG/BannSEG/StanSEG, no notice has named the exact HoumaSEG
  points yet — verify at the desk before treating as certain.
- `operational_capacity_fact` columns — finalize against a real OAC/OA_MLC table
  (Egan/Bobcat capacity pages are on `rtba.enbridge.com`, a different subdomain/likely
  JS app — untested).
- LLM extraction step — the eval harness + regex baseline now exist
  (`src/nge/extract/`); the LLM extractor implements the same interface and must beat the
  baseline on the growing gold set. Needs `ANTHROPIC_API_KEY` in the environment.
- Minor pre-existing data-quality artifact (not introduced this session): one Sabine
  points row (`loc=1604`, "Chemical Waste Management") has `Up/Dn Ind=N` but a literal
  `"N"` in the FERC CID column position, which the resolver currently passes through as
  if it were a real CID. Low-impact (1 row); worth a small `resolve.py` hardening pass
  (validate CID shape, or gate on `updn_ind=='Y'`) whenever resolve.py is next touched.
