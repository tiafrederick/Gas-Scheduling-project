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
| DDL-014 | **Deterministic core, LLM shell** (Era 2 governing principle). All *selection* logic — which assets/points/contracts/events are affected, severity, confidence — is deterministic SQL/graph traversal: auditable, testable, cited. LLM confined to (1) extraction, (2) narration over deterministically-assembled inputs, (3) NL→intent translation. **Citation gate** generalizes the extraction span gate to generated text: every sentence must cite uids present in its input bundle; a deterministic verifier strips uncited sentences, unknown uids fail the narration entirely (template fallback). Every capability fully functional with **no API key**; no-LLM mode is the default test mode. | Accepted | Design: `docs/operational-intelligence.md` §1.1–1.2, §1.5. The trust properties that make Era 1 auditable must survive the introduction of generation. |
| DDL-015 | **Graph is a projection, not a store** (reaffirms DDL-001/010). `nge/graph.py` builds an in-memory typed graph (stdlib adjacency; no networkx in core) from canonical tables on demand. Node kinds: pipeline/point/segment/storage_facility/market_hub; edge kinds: interconnect/on_segment/of_pipeline/storage_service/hub_member. New curated seed tables `market_hub` + `hub_member` (same confidence+evidence governance as DDL-013). **Path confidence = min(edge confidences)** — weakest-link, matching scheduler reasoning; product would punish long solid paths and these are trust grades, not independent probabilities. Hop count always reported. | Accepted | Design: OI doc §3. Node/edge identity = canonical keys; the graph invents no identity. **As implemented (`nge/graph.py`, OI-2):** interconnect edges carry flow direction derived from Dir Flo (R/D/B → in/out/both) so explanations speak scheduler language; retired points flagged `[RETIRED]` and excluded from default pathfinding (active_only); one-sided declarations get traversal mirrors, roundtrip pairs keep both TSPs' own cited rows; `paths()` default min_conf=0.7 sits deliberately above the 0.6 lead tier (leads inform, they don't route); storage operators typed via a curated `STORAGE_OPERATOR_CIDS` registry. |
| DDL-016 | **Operational events are derived, then materialized.** `operational_event` + `event_impact` are deterministic projections of (notice × capacity_impact_fact × segment_asset_map × interconnect), persisted for timeline/brief performance, rebuildable from scratch. Status machine `planned→active→completed|superseded` derived from notice lifecycle fields, observed subject idioms (`COMPLETED:`/`UPDATE:`/`REVISED`), `prior_notice_id` chains, and gas-day windows. Supersession keeps both events, linked — correction history is operational signal. | Accepted | Design: OI doc §4.4–4.5. Requires the OI-1 notice fixture corpus (~8–12 from the already-uploaded `CGT Notices.pdf`) — the one architecturally-justified ingestion increment of Era 2 (fixture labeling, dual-purpose with the extraction gold set; no new parser infrastructure). **As implemented (`nge/events.py`, OI-1):** chains key on the subject's *facility phrase*, not the SEG code (Banner and New Albany are both BannSEG but separate jobs); the stored column is time-INdependent `lifecycle_status` and operational status is the pure function `status_at(window, lifecycle, as_of)` — keeps derivation deterministic and goldens time-stable; window sources never blended (fact 0.97 > subject 0.85 > effective_date 0.60, source recorded); an effective date may open a window but never close one (a dateless FM UPDATE must not shrink an open-ended event — bug caught by the event gold pre-commit). |
| DDL-017 | **Constraint propagation emits risks + investigations, never predictions.** Public EBB data cannot yield actual flow outcomes — only *exposure* and *what to verify*. Direction-aware bounded BFS (default 2 commercial hops) from constrained assets; severity = max(quantitative band from cut% = 1−setting/design: <5% informational, 5–15% watch, >15% action; qualitative floors: Secondary Firm ≥watch, Primary Firm ≥action, FM/OFO ⇒ critical), decaying one band per hop; per-hop confidence = min-chain. Outputs carry `reason_code` + `recommended_investigation` (human instruction) + full citation chain. | Accepted | Design: OI doc §4. Enforced in type names (`PropagatedRisk`, `investigation`) — human judgment stays central (DDL-005 spirit). AlexSEG is the calibration case: 13.4% cut (watch) + Primary Firm language ⇒ action. |
| DDL-018 | **Morning Brief = deterministic assembly → explainable ranking → template render → optional LLM polish behind the citation gate.** Ranking score = severity_weight × exposure_factor(BP MDQ at affected points) × novelty(since last `brief_run`) × confidence, all components printed in `--explain` mode. Template mode is byte-stable and golden-tested; low-confidence *critical* items are floored into the brief with an "unconfirmed — verify first" tag (missing a real FM is the worse failure mode). | Accepted | Design: OI doc §6. Weights are explicit hypotheses in one constants block; changes show up as reviewable golden-file diffs. |
| DDL-019 | **NL Query = intent registry, not text-to-SQL.** ~8 whitelisted typed intents (asset_impact, events_in_window, path_between, point_lookup, contract_exposure, interconnect_partners, capacity_at_point*, storage_status*; *=honest stubs until OAC/storage data exists), each backed by a deterministic, cited executor. LLM router via tool-use schema (must pick a registered intent or `out_of_scope`); no-key fallback = keyword router over the same registry; routing confidence and answer confidence reported separately, never blended. Out-of-scope ⇒ explicit refusal listing what *can* be asked. | Accepted | Design: OI doc §7. Free-form text-to-SQL rejected: unauditable, injection-prone, untestable. The registry doubles as the future UI/MCP contract. |
| DDL-020 | **Service layer = in-process typed facade (`nge/api.py`) + thin CLI wrappers; no HTTP until the UI era.** Request/response dataclasses are the stable contract; every OI API takes (`as_of` gas day, optional `as_known` timestamp), making bitemporal queries first-class. FastAPI later wraps the same facade unchanged. | Accepted | Design: OI doc §1.4, roadmap OI-7. Premature HTTP infrastructure rejected; the facade is the contract. |

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
- **Era 2 backlog is tracked in [`docs/roadmap.md`](./roadmap.md)** (milestones
  OI-1…OI-7 with issues, dependencies, DoD); design authority is
  [`docs/operational-intelligence.md`](./operational-intelligence.md). Items below are
  cross-cutting concerns not owned by a single milestone.
- **`segment_asset_map` coverage** — 4 CGT segments mapped; every additional mapped
  segment (and eventually SESH/Sabine equivalents, which have no seg-cd column and
  will need a different evidence path) raises impact precision. Grows with observed
  notices, not code.
- **`market_hub` / `hub_member` curation** (new in OI-2) — seed rows are
  desk-verifiable claims with confidence + evidence, same governance as DDL-013;
  review them like tariff data, not like code.
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
- LLM extraction step — the eval harness + regex baseline exist (`src/nge/extract/`);
  the LLM extractor implements the same interface and must beat the baseline on the
  gold set grown by roadmap issue OI-1.1. The provider-agnostic client it needs
  (`nge/llm.py`) is built in OI-6.1. Needs `ANTHROPIC_API_KEY` in the environment.
- Minor pre-existing data-quality artifact (not introduced this session): one Sabine
  points row (`loc=1604`, "Chemical Waste Management") has `Up/Dn Ind=N` but a literal
  `"N"` in the FERC CID column position, which the resolver currently passes through as
  if it were a real CID. Low-impact (1 row); worth a small `resolve.py` hardening pass
  (validate CID shape, or gate on `updn_ind=='Y'`) whenever resolve.py is next touched.
