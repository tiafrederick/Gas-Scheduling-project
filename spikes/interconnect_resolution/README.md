# Spike: Cross-Pipeline Interconnect Resolution

**Question this spike answers:** can we walk "an event on CGT → the SESH delivery
points BP cares about" purely from public point data, with no hand-maintained
mapping — and know *how confident* each hop is?

**Answer:** yes. Each pipeline's point posting already declares its counterparty's
`FERC CID` + `Loc`. Keying points by the composite `(tsp_ferc_cid, loc)` and
resolving those declarations yields the interconnect graph. The known
**SESH `83004` (COLUMBIA GULF - DELHI) ↔ CGT `4208` (SESH)** pair resolves
bidirectionally at confidence 1.0.

## Run
```bash
python3 spikes/interconnect_resolution/resolve.py
python3 -m unittest tests.test_interconnect_resolution -v
```

## Inputs
- `data/samples/sesh_all_points.csv` — real SESH InfoPost point export (public).
- `data/samples/sabine_locations.csv` — real Sabine/gasnom point export (public).
- `data/fixtures/cgt_points_sample.csv` — **fixture**: one CGT point (`4208`), the
  reciprocal of SESH `83004`, hand-built pending a parse of `CGT Location Data.pdf`.

## Resolution tiers (confidence)
| status | conf | meaning |
|---|---|---|
| `resolved_roundtrip` | 1.0 | both sides declare each other — trust it |
| `resolved_cid_loc` | 0.9 | counterparty point present, no reciprocal mirror |
| `resolved_cid_only` | 0.6 | counterparty **pipeline** known, its point not yet ingested |
| `declared_external` | 0.4 | counterparty CID declared but uncatalogued |
| `unresolved` | 0.0 | loc but no CID |
| `unresolved_no_counterparty` | 0.0 | endpoint / intra-system (no Up/Dn keys) |

## What it proves for the architecture
1. **DDL-002 keys are correct** — composite `(cid, loc)` + `Up/Dn` declarations are sufficient.
2. **Per-source adapters are required** — SESH and Sabine ship different column layouts
   (handled here by `SESH_LAYOUT` / `SABINE_LAYOUT`); this is risk #7 (schema drift) made concrete.
3. **`resolved_cid_only` is a backlog generator** — it names exactly which pipelines'
   point catalogs to ingest next (Enable, Gulf South, TETLP, Transco, TGP, SNG, …).

## What it deliberately does NOT do
No DB, no ingestion pipeline, no LLM, no fuzzy name matching yet. Those are later
phases. This is a logic proof on real data.
