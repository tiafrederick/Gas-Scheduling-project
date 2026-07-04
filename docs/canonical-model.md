# Canonical Model — entities, keys, and bitemporality

Authoritative DDL: [`schema/canonical.sql`](../schema/canonical.sql). Typed models:
[`src/nge/models/facts.py`](../src/nge/models/facts.py).

## Bitemporal convention (DDL-003)
Two independent time axes, because "what was true" and "what we knew" diverge constantly
in scheduling (a notice gets revised; a rate's effective date differs from when it posted):

- **valid time** (`valid_*`, `term_*`, `gas_day`) — when a fact is true in the real world.
- **system time** (`system_*`, `system_recorded_at`) — when *we* recorded/last-saw it.
- Current version of a mutable row ⇒ `system_to IS NULL`.
- Canonical query shape: *"AlexSEG available capacity **as-of gas day 2026-07-08** **as-known
  2026-07-01**."* Both axes are needed to reconstruct what you would have decided at the time.
- Notices are **append-only events** (`system_recorded_at` only). The *assertions* they make
  are valid-timed in `capacity_impact_fact`.

## Entities
| table | grain | key |
|---|---|---|
| `pipeline` | one TSP | `ferc_cid` |
| `point` | one location on one pipe | `point_uid = tsp_ferc_cid || ':' || loc` |
| `interconnect` | one declared counterparty edge | hash of a-point + b keys |
| `contract_holding` | one contract (K number) | `holding_uid`; valid time = term |
| `contract_point` | point on a holding | (holding, point) |
| `notice` | one EBB notice (immutable) | `tsp_ferc_cid || ':' || notice_id` |
| `capacity_impact_fact` | one extracted number | `fact_uid`; valid time = gas-day window |
| `rate_fact` | one rate component | `rate_uid`; valid time = effective window |
| `operational_capacity_fact` | point × gas_day × cycle | `cap_uid` (shape stubbed) |

## Interconnect resolution tiers
The `interconnect.resolution_status` / `resolution_confidence` values, proven in the spike:

| status | conf | meaning | action |
|---|---|---|---|
| `resolved_roundtrip` | 1.0 | both sides declare each other | trust |
| `resolved_cid_loc` | 0.9 | counterparty point present, no mirror | trust, flag for review |
| `resolved_cid_only` | 0.6 | counterparty **pipeline** known, point not ingested | ingest that pipe next |
| `declared_external` | 0.4 | CID declared but uncatalogued | backlog |
| `unresolved` / `unresolved_no_counterparty` | 0.0 | no CID / endpoint | none |

Never silently drop an unresolved edge — surface it. A dropped interconnect is a blind
spot in impact analysis (risk #1).

## Design rules
- **No bare numbers.** Every `capacity_impact_fact` / `rate_fact` carries `source_span`
  (exact substring), `extraction_method`, `confidence`, and a `verified_by` slot.
- **Fail loud on schema drift.** Parsers validate against controlled vocabularies
  (`METRICS`, `CYCLES`, `DIRECTIONS`, …) and raise, not coerce (risk #7).
- **Graph is derived.** Nothing writes to a graph store; reachability reads `interconnect`.

## Open modeling questions (deferred)
- Storage (Egan / Bobcat / Sabine Hub) balance facts — no data yet; likely a distinct
  `storage_balance_fact` grain (inventory, injection/withdrawal, ratchets vs. contract).
- Capacity-screen (`operational_capacity_fact`) columns — finalize when we parse a real OAC/OA_MLC table.
- Compressor-segment ↔ point mapping (AlexSEG → which CGT points?) — needs CGT location data.
