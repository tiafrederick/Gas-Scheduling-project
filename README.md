# Natural Gas Scheduling Knowledge Engine

An AI-assisted operations-intelligence foundation for a Southeast-Gulf natural gas
scheduler (CGT · SESH · Sabine · Egan/Bobcat/Sabine Hub). It ingests pipeline
operational data (EBB notices, capacity postings, maintenance/outages, tariffs,
contracts), normalizes it into one **canonical, bitemporal, provenance-tracked** model,
and enables **AI-driven impact analysis** — e.g. *"how does this CGT/AlexSEG maintenance
affect SESH and my deliveries?"*

> **Status: Phase 1 — design-first.** No UI, no automated ingestion, no full pipelines yet.
> The priority is architecture, correctness, and maintainability. Human judgment stays at
> the center of every scheduling action.

## Why this exists
Schedulers lose time hopping between EBBs, tariffs, maps, and notices to reason about
downstream effects. This engine builds the model *once*, keeps it correct over time
(bitemporal + provenance), and lets an AI reason over the model instead of over raw PDFs.

## What's here now
| Path | What |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Layered architecture + the relational-core/derived-graph decision |
| [`docs/data-sources.md`](docs/data-sources.md) | Source inventory, format families, the FERC-CID/loc keys that join them |
| [`docs/canonical-model.md`](docs/canonical-model.md) | Entities, bitemporal convention, interconnect resolution tiers |
| [`docs/extraction-schema.md`](docs/extraction-schema.md) | Notice→typed-facts schema, worked on the AlexSEG notice |
| [`docs/eval-approach.md`](docs/eval-approach.md) | How each layer is measured for correctness |
| [`docs/design-decision-log.md`](docs/design-decision-log.md) | Living decision log (DDL-001…013) |
| [`schema/canonical.sql`](schema/canonical.sql) | Bitemporal DDL (DuckDB dialect) |
| [`src/nge/models/facts.py`](src/nge/models/facts.py) | Typed fact models (`Notice`, `CapacityImpactFact`) |
| [`src/nge/tools/parse_cgt_locations.py`](src/nge/tools/parse_cgt_locations.py) | CGT's TC eConnects location PDF → point catalog CSV |
| [`spikes/interconnect_resolution/`](spikes/interconnect_resolution/) | Runnable proof: cross-pipeline interconnect resolution |
| [`data/samples/`](data/samples/) · [`data/fixtures/`](data/fixtures/) | Real public CSV exports (all 5 portfolio pipes) + AlexSEG notice + gold labels |
| [`tests/`](tests/) | Round-trip resolution, loader, and cited-reachability tests |

## Run it
```bash
pip install duckdb pydantic          # core stack (DDL-011); spike/tests run stdlib-only

# Build the canonical DuckDB store from landed samples/fixtures
PYTHONPATH=src python3 -m nge.load

# The v1 vertical slice: cited cross-pipeline impact analysis
PYTHONPATH=src python3 -m nge.reach --asset AlexSEG

# Prove the cross-pipeline interconnect resolves both ways (stdlib only)
python3 spikes/interconnect_resolution/resolve.py

# Tests (17: entity resolution + extraction eval + loader + cited reachability)
python3 -m unittest discover -s tests -v
```

## Data boundary
Personal/local, **public FERC informational-postings data only**. Cloud LLM (Fable 5) is
acceptable because the data is public. Nomination volumes / positions / trade intent are
**out of scope** in this configuration.

## Roadmap (high level)
1. ✅ Phase 0 — architecture discovery + decision log.
2. ✅ Phase 1 — canonical schema, entity-resolution proof, extraction schema, eval plan.
3. ✅ Phase 2 — stack locked (Python+DuckDB), canonical store loads, **cited impact
   analysis works end-to-end** (`nge.reach`).
4. ✅ Phase 3 — extraction eval harness + regex baseline (`nge.extract`); **all 5
   portfolio pipes' point catalogs landed and real** (SESH, Sabine, Egan, Bobcat via
   Firecrawl; CGT parsed from its TC eConnects PDF). Two new confidence-1.0
   cross-pipeline round-trips: CGT↔Egan, CGT↔Sabine. Segment→asset mapping
   (`segment_asset_map`, DDL-013) lets `nge.reach` cite CGT's *specific points*
   (e.g. `4208D`/`4208R`) for an asset like AlexSEG, not just "the whole pipeline."
   Along the way, found a genuine cross-EBB data-staleness case (SESH references
   CGT's retired point `4208` instead of the split `4208D`/`4208R`) and surfaced it
   rather than hiding it. Open gaps: Sabine Hub Services (distinct entity from Sabine
   Pipe Line, EBB location unconfirmed), storage-balance fact schema — see
   `docs/design-decision-log.md` open items.
5. ⬜ Phase 4 (remaining) — LLM extraction step vs. the regex baseline on a growing
   gold set; resolve Sabine Hub Services; storage-balance fact schema; broaden
   ingestion to more counterparty point catalogs named by `resolved_cid_only` edges.
6. ⬜ Later — minimal operational workspace UI.
