# Architecture — Natural Gas Scheduling Knowledge Engine

> Phase 1 (design-first). No UI, no automated ingestion, no full pipelines yet.
> Priority: architecture, correctness, maintainability > implementation speed.

## The one-paragraph thesis
Turn three fragmented EBB worlds (CGT / TC eConnects, SESH / Enbridge InfoPost,
Sabine / gasnom) into **one canonical, bitemporal, provenance-tracked model** of the
Southeast-Gulf portfolio's points, interconnects, contracts, rates, and events — then
let an AI reason over that *model* (not over raw PDFs) to answer impact-analysis
questions like *"how does this CGT/AlexSEG maintenance affect SESH and my deliveries?"*

## Layers
```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 7. Access        notebooks / CLI now   ·   UI LATER (deferred)        │
 ├─────────────────────────────────────────────────────────────────────┤
 │ 6. Reasoning     impact analysis · reachability · opportunity spotting│
 │                  provider-agnostic LLM (Fable 5 reasoning) — DDL-004  │
 ├─────────────────────────────────────────────────────────────────────┤
 │ 5. Knowledge     interconnect graph = DERIVED read-model              │
 │    graph         (recursive SQL / networkx, NOT a graph DB) — DDL-010 │
 ├─────────────────────────────────────────────────────────────────────┤
 │ 4. Canonical     bitemporal system of record (DuckDB) — DDL-001       │
 │    model         pipeline·point·interconnect·contract·notice·facts    │
 ├─────────────────────────────────────────────────────────────────────┤
 │ 3. Parse/Extract deterministic parsers (CSV/tables) +                 │
 │                  LLM-assisted extraction of notice prose → typed facts│
 ├─────────────────────────────────────────────────────────────────────┤
 │ 2. Raw landing   immutable, content-addressed originals + provenance  │
 ├─────────────────────────────────────────────────────────────────────┤
 │ 1. Acquisition   per-platform adapters. v1 = MANUAL drops (DDL-006);  │
 │                  automation designed but OFF.                         │
 └─────────────────────────────────────────────────────────────────────┘
 Cross-cutting: entity resolution · temporal/versioning · provenance/audit ·
                data-quality tests · LLM abstraction
```

## Why relational-core + derived graph (not graph-first)
A property graph is excellent for *reachability queries* but a poor **system of
record** for versioned, temporal, auditable facts (revised notices, rate effective
windows, contract terms). We keep authority in a bitemporal relational store and
project the graph from the `interconnect` table on demand. At portfolio scale (3
pipes + direct interconnects) that projection is recursive SQL or an in-memory
`networkx` graph — a dedicated graph DB (Neo4j) would be operational cost for no
benefit. See DDL-001 / DDL-010.

## Data boundary (DDL-007)
Personal/local. **Public FERC informational-postings data only** (points, capacity,
notices, tariffs) plus public Index-of-Customers holdings. Because that data is
public, a cloud LLM (Fable 5) is acceptable. **Guardrail:** actual nomination
volumes / positions / trade intent beyond public postings must not enter this
configuration.

## v1 vertical slice
CGT AlexSEG maintenance (notice `26092015`) → affected CGT asset/points →
`interconnect` edges → SESH points where BP holds FTS (contract `840245-R1`) →
plain-language impact. The hard part (entity resolution) is proven in
`spikes/interconnect_resolution/`.

## Repo map
```
schema/canonical.sql            bitemporal DDL (DuckDB dialect)
src/nge/models/facts.py         typed fact models (Notice, CapacityImpactFact)
spikes/interconnect_resolution/ runnable ER proof (stdlib) + README
data/samples/                   real public CSV exports (SESH, Sabine)
data/fixtures/                  AlexSEG notice + gold extraction labels + CGT point
tests/                          round-trip resolution test
docs/                           this + data-sources, canonical-model, extraction, eval, DDL
```

## Explicitly out of scope for v1
Storage-balance modeling (no data yet), imbalance tracking, broader regional
network, hydraulic simulation, automated scraping, and any UI.
