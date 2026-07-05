# Natural Gas Scheduling Knowledge Engine

An AI-assisted operations-intelligence foundation for a Southeast-Gulf natural gas
scheduler (CGT · SESH · Sabine · Egan/Bobcat/Sabine Hub). It ingests pipeline
operational data (EBB notices, capacity postings, maintenance/outages, tariffs,
contracts), normalizes it into one **canonical, bitemporal, provenance-tracked** model,
and enables **AI-driven impact analysis** — e.g. *"how does this CGT/AlexSEG maintenance
affect SESH and my deliveries?"*

> **Status: Era 1 (knowledge engine) complete — Era 2 (operational intelligence) designed
> and ready to build.** Design: [`docs/operational-intelligence.md`](docs/operational-intelligence.md).
> Backlog: [`docs/roadmap.md`](docs/roadmap.md) (milestones OI-1…OI-7). No UI yet.
> The priority is architecture, correctness, explainability, and maintainability.
> Human judgment stays at the center of every scheduling action.

## Why this exists
Schedulers lose time hopping between EBBs, tariffs, maps, and notices to reason about
downstream effects. This engine builds the model *once*, keeps it correct over time
(bitemporal + provenance), and lets an AI reason over the model instead of over raw PDFs.

## What's here now
| Path | What |
|---|---|
| [`docs/operational-intelligence.md`](docs/operational-intelligence.md) | **Era 2 design**: impact engine, relationship graph, constraint propagation, timeline, morning brief, NL query — 6 capabilities × 9 aspects |
| [`docs/roadmap.md`](docs/roadmap.md) | **Backlog**: epics OI-1…OI-7 with issues, dependency order, definition of done |
| [`docs/architecture.md`](docs/architecture.md) | Layered architecture + the relational-core/derived-graph + deterministic-core/LLM-shell decisions |
| [`docs/data-sources.md`](docs/data-sources.md) | Source inventory, format families, the FERC-CID/loc keys that join them |
| [`docs/canonical-model.md`](docs/canonical-model.md) | Entities, bitemporal convention, interconnect resolution tiers |
| [`docs/extraction-schema.md`](docs/extraction-schema.md) | Notice→typed-facts schema, worked on the AlexSEG notice |
| [`docs/eval-approach.md`](docs/eval-approach.md) | How each layer is measured for correctness |
| [`docs/design-decision-log.md`](docs/design-decision-log.md) | Living decision log (DDL-001…020) |
| [`schema/canonical.sql`](schema/canonical.sql) | Bitemporal DDL (DuckDB dialect) |
| [`src/nge/models/facts.py`](src/nge/models/facts.py) | Typed fact models (`Notice`, `CapacityImpactFact`) |
| [`src/nge/tools/parse_cgt_locations.py`](src/nge/tools/parse_cgt_locations.py) | CGT's TC eConnects location PDF → point catalog CSV |
| [`spikes/interconnect_resolution/`](spikes/interconnect_resolution/) | Runnable proof: cross-pipeline interconnect resolution |
| [`data/samples/`](data/samples/) · [`data/fixtures/`](data/fixtures/) | Real public CSV exports (all 5 portfolio pipes) + AlexSEG notice + gold labels |
| [`tests/`](tests/) | Round-trip resolution, loader, and cited-reachability tests |

## Run it
```bash
pip install duckdb pydantic          # core stack (DDL-011); spike/tests run stdlib-only

# Build the canonical DuckDB store (points, interconnects, contracts, notices,
# hub seeds, and 11 derived operational events from the 15-notice corpus)
PYTHONPATH=src python3 -m nge.load

# The Pipeline Relationship Graph — the reasoning engine's foundational model
PYTHONPATH=src python3 -m nge.graphq stats
PYTHONPATH=src python3 -m nge.graphq explain C000307:4123 C000086:45103   # CGT <-> Egan, cited + flow-worded
PYTHONPATH=src python3 -m nge.graphq neighbors C000307:519                # CGT's Henry Hub point

# Cited cross-pipeline impact analysis (now graph-backed)
PYTHONPATH=src python3 -m nge.reach --asset AlexSEG

# Prove the cross-pipeline interconnect resolves both ways (stdlib only)
python3 spikes/interconnect_resolution/resolve.py

# Tests (41: resolution, extraction eval, loader, reach golden, graph, events)
python3 -m unittest discover -s tests -v
```

## Data boundary
Personal/local, **public FERC informational-postings data only**. Cloud LLM (Fable 5) is
acceptable because the data is public. Nomination volumes / positions / trade intent are
**out of scope** in this configuration.

## Roadmap

**Era 1 — Knowledge Engine: ✅ complete** (repo phases 0–4, commits `fbea0bc`…`44d7686`).
Canonical bitemporal store · cross-pipeline entity resolution with confidence tiers
(two 1.0 round-trips: CGT↔Egan, CGT↔Sabine; one genuine cross-EBB staleness finding
surfaced: SESH still references CGT's retired point `4208`) · all 5 portfolio pipes'
point catalogs landed and real · segment→asset mapping (DDL-013) · cited point-level
impact analysis · extraction eval harness with span gate · 17/17 tests.

**Era 2 — Operational Intelligence: 🎯 designed, ready to build.** Ingestion breadth is
frozen; the complexity budget moves to reasoning. Six capabilities — Operational Impact
Engine, Pipeline Relationship Graph, Constraint Propagation, Operational Timeline,
Morning Brief, NL Query — governed by *deterministic core / LLM shell* (DDL-014),
*exposure-not-prediction* (DDL-017), and *works-without-an-API-key* principles.
Full design: [`docs/operational-intelligence.md`](docs/operational-intelligence.md).
Milestones OI-1…OI-7 with dependencies and definitions of done:
[`docs/roadmap.md`](docs/roadmap.md).

**Era 3 — horizon (not committed):** scheduled brief delivery, OAC/storage-balance
ingestion (activates quantitative propagation), multi-turn copilot, minimal
operational workspace UI over the OI facade.
