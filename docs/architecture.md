# Architecture — Natural Gas Scheduling Knowledge Engine

> Era 1 (knowledge engine: layers 1–5 below) is **built** — commits `fbea0bc`…`44d7686`,
> 17/17 tests. Era 2 (operational intelligence: layer 6) is **designed** — see
> [`docs/operational-intelligence.md`](./operational-intelligence.md) and the OI-1…OI-7
> backlog in [`docs/roadmap.md`](./roadmap.md). UI (layer 7) remains deferred.
> Priority: architecture, correctness, maintainability > implementation speed.

## The one-paragraph thesis
Turn the fragmented EBB worlds of a Southeast-Gulf portfolio (CGT / TC eConnects; SESH,
Egan, Bobcat / Enbridge InfoPost; Sabine / gasnom) into **one canonical, bitemporal,
provenance-tracked model** of points, interconnects, contracts, rates, and events —
then let a **deterministic reasoning core with an LLM shell** operate on that *model*
(never on raw PDFs) to answer a scheduler's operational questions: *what does this
maintenance touch, how bad is it, what should I check, what changed overnight?*

## Layers
```
 ┌───────────────────────────────────────────────────────────────────────┐
 │ 7. Access        CLI per capability now · nge/api.py facade (OI-7)     │
 │                  UI LATER (wraps the facade unchanged) — DDL-020       │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 6. OPERATIONAL   impact engine (nge/impact) · constraint propagation   │
 │    INTELLIGENCE  (nge/propagate + operational_event/event_impact) ·    │
 │    (Era 2,       timeline (nge/timeline) · morning brief (nge/brief) · │
 │     designed)    NL query via intent registry (nge/ask + nge/llm)      │
 │                  deterministic core, LLM shell + citation gate—DDL-014 │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 5. Knowledge     typed graph projection (nge/graph, OI-2): pipelines · │
 │    graph         points · segments · storage · market hubs; DERIVED,   │
 │                  never a store — DDL-010/015                           │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 4. Canonical     bitemporal system of record (DuckDB) — DDL-001        │
 │    model         pipeline·point·interconnect·segment_asset_map·        │
 │                  contract·notice·capacity facts (+ events in OI-1)     │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 3. Parse/Extract deterministic parsers (CSV + PDF tools) + LLM-assist  │
 │                  extraction w/ span gate (nge/extract) — DDL-003       │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 2. Raw landing   immutable originals + .meta.json provenance sidecars  │
 ├───────────────────────────────────────────────────────────────────────┤
 │ 1. Acquisition   per-platform adapters; manual drops + Firecrawl for   │
 │                  public postings — DDL-006/012. Breadth FROZEN in Era 2│
 └───────────────────────────────────────────────────────────────────────┘
 Cross-cutting: entity resolution w/ confidence tiers · bitemporal (as_of/as_known)
 semantics · provenance/audit ("no un-cited hop") · confidence algebra (min-composition)
 · data-quality surfacing · provider-agnostic LLM boundary (DDL-004)
```

## Why relational-core + derived graph (not graph-first)
A property graph is excellent for *reachability queries* but a poor **system of
record** for versioned, temporal, auditable facts (revised notices, rate effective
windows, contract terms). Authority stays in the bitemporal relational store; the
typed graph is projected from `interconnect` + `segment_asset_map` + seed tables on
demand (stdlib adjacency — a graph DB would be operational cost for no benefit at
portfolio scale). See DDL-001 / DDL-010 / DDL-015.

## Why deterministic core + LLM shell (Era 2's governing split)
Everything that *selects* — affected assets, severity, confidence, exposure — is SQL
and graph traversal: testable, auditable, cited. The LLM only extracts (span-gated),
narrates (citation-gated), and translates questions into whitelisted intents. Every
capability works without an API key; generation can polish the answer but can never
be the source of a fact. See DDL-014/017/018/019.

## The service facade (Access layer, OI-7 · DDL-020)
`nge/api.py` is the **only** public interface. `Engine` is a session object that owns
the DuckDB connection (read-only by default) and a cached graph build, and exposes one
method per capability — `impact` · `timeline` · `brief` · `ask` · `path` · `neighbors`
· `graph_stats` — each returning a typed `*Response` with a uniform envelope
(`capability`, `as_of`, `as_known`, `error`). `render()` delegates to the wrapped
capability object, so the facade standardizes the *contract* without changing any
rendered output. The bitemporal `(as_of, as_known)` pair is first-class and enforced
honestly: `as_known` is threaded only into `timeline` (which reconstructs history) and
**raises** `AsKnownUnsupported` elsewhere rather than return a current-knowledge answer
under a historical query; the graph is atemporal. Bad input degrades to a graceful
`error` string — the facade never leaks an internal exception to a caller. CLIs are
thin wrappers over `Engine`; a future FastAPI/MCP layer wraps the same object
unchanged. (`nge.reach` remains a deprecated *internal* Era-1 golden anchor, excluded
from the facade — see the DDL log open items.)

## Data boundary (DDL-007)
Personal/local. **Public FERC informational-postings data only** (points, capacity,
notices, tariffs) plus public Index-of-Customers holdings. Because that data is
public, a cloud LLM is acceptable. **Guardrail:** actual nomination volumes /
positions / trade intent beyond public postings must not enter this configuration;
shipper-login data stays manual-drop only and credentials never enter the container
(DDL-012).

## The proven vertical slice
CGT AlexSEG maintenance (notice `26092015`) → extracted facts → ALEXDRIA segment
(0.9, evidence-tagged) → its 14 points incl. `4208D/4208R` → SESH `83004/83104`
(0.9 — SESH's posting is stale, surfaced not hidden) → BP FTS `840245-R1`
(27,000 Dth/d) → cited exposure + investigations. Era 2 milestones each extend this
same golden scenario (events → propagation → timeline → brief → NL answer).

## Repo map
```
schema/canonical.sql              bitemporal DDL (DuckDB dialect)
src/nge/resolve.py                cross-pipeline entity resolution (stdlib, tiers)
src/nge/store.py · load.py        DuckDB store build + loaders
src/nge/reach.py                  cited impact analysis (→ nge/impact.py in OI-3)
src/nge/extract/                  extraction eval harness + span-gated baseline
src/nge/models/facts.py           typed fact models
src/nge/tools/                    offline data-prep tools (CGT PDF parser)
spikes/interconnect_resolution/   runnable ER proof + findings report
data/raw/<portal>/<date>/         landed originals + provenance sidecars
data/samples/ · data/fixtures/    normalized exports (5 pipes) · gold labels
tests/                            17 tests incl. can-fail tests for every gate
docs/operational-intelligence.md  Era 2 design (6 capabilities × 9 aspects)
docs/roadmap.md                   backlog: epics OI-1…OI-7, dependencies, DoD
```

## Explicitly out of scope for Era 2
UI (deferred until the facade exists) · HTTP services · hydraulic simulation ·
imbalance/storage-balance modeling (no data; schema slots reserved) · new ingestion
breadth (frozen except the OI-1 notice-fixture corpus) · nomination/position data of
any kind (boundary, not backlog).
