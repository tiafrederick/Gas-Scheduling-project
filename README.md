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
| [`docs/design-decision-log.md`](docs/design-decision-log.md) | Living decision log (DDL-001…011) |
| [`schema/canonical.sql`](schema/canonical.sql) | Bitemporal DDL (DuckDB dialect) |
| [`src/nge/models/facts.py`](src/nge/models/facts.py) | Typed fact models (`Notice`, `CapacityImpactFact`) |
| [`spikes/interconnect_resolution/`](spikes/interconnect_resolution/) | Runnable proof: SESH↔CGT interconnect resolution |
| [`data/samples/`](data/samples/) · [`data/fixtures/`](data/fixtures/) | Real public CSV exports + AlexSEG notice + gold labels |
| [`tests/`](tests/) | Round-trip resolution test |

## Run it (zero dependencies — stdlib Python 3.11+)
```bash
# Prove the cross-pipeline interconnect resolves both ways
python3 spikes/interconnect_resolution/resolve.py

# Tests
python3 -m unittest discover -s tests -v

# Validate the typed models against the AlexSEG gold labels
PYTHONPATH=src python3 -c "import json; from nge.models import CapacityImpactFact; \
d=json.load(open('data/fixtures/cgt_notice_26092015.expected_facts.json')); \
print(len([CapacityImpactFact(**f) for f in d['capacity_impact_facts']]), 'facts validated')"
```

## Data boundary
Personal/local, **public FERC informational-postings data only**. Cloud LLM (Fable 5) is
acceptable because the data is public. Nomination volumes / positions / trade intent are
**out of scope** in this configuration.

## Roadmap (high level)
1. ✅ Phase 0 — architecture discovery + decision log.
2. ▶ Phase 1 — canonical schema, entity-resolution proof, extraction schema, eval plan *(this commit)*.
3. ⬜ Phase 2 — DuckDB load + ingest more counterparty point catalogs (shrink unresolved edges).
4. ⬜ Phase 3 — LLM extraction step + provider-agnostic reasoning over the model.
5. ⬜ Later — minimal operational workspace UI.
