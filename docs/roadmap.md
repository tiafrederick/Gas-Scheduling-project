# Project Roadmap & Backlog

> This markdown backlog is **canonical**. If/when GitHub Issues are enabled for this
> repo (requires the GitHub MCP connector to be authorized), each `### Issue` block
> below imports 1:1 as an issue, each `## Epic` as a milestone/label. Design authority:
> [`docs/operational-intelligence.md`](./operational-intelligence.md) (the "OI doc").

---

## Era 1 — Knowledge Engine (COMPLETE)

| Phase | Delivered | Commit |
|---|---|---|
| 0–1 | Architecture, canonical bitemporal schema, ER spike, extraction schema, eval plan | `fbea0bc` |
| 2 | DuckDB store, cited impact analysis (`nge.reach`), Egan/Bobcat onboarding | `8d35329` |
| 3 | Extraction eval harness + regex baseline (span gate), Egan/Bobcat landed live (Firecrawl), real FERC CIDs | `334c3c2`, `87dd9c4` |
| 4 | CGT point catalog (153 pts) parsed + landed, segment→asset mapping (DDL-013), point-level cited impact, 2 new 1.0 round-trips, SESH staleness finding | `44d7686` |

17/17 tests green. Ingestion breadth is **frozen** except where an OI milestone
declares an architectural need (only OI-1 does).

---

## Era 2 — Operational Intelligence (THIS ERA)

### Dependency graph & recommended order

```
OI-1 Event Foundation ──────┬──► OI-3 Constraint Propagation ──► OI-5 Morning Brief
        │                   │              ▲                          ▲
        └──► OI-4 Timeline ─┼──────────────┘ (severity+impacts)      │
                            │                                        │
OI-2 Relationship Graph ────┴──► (OI-3 traversal)                    │
        │                                                            │
        └────────────► OI-6 LLM Client + NL Query ◄──────────────────┘
                                   │
                                   ▼
                       OI-7 API Facade + Consolidation
```

**Order:** OI-1 ∥ OI-2 (parallel) → OI-3 → OI-4 (may start right after OI-1, parallel
to OI-3) → OI-5 → OI-6 → OI-7. Strict rule: a milestone starts only when its
dependencies' DoD is met.

### Uniform Definition of Done (applies to every milestone, plus per-milestone items)

- [ ] All tests green, including the milestone's new golden/property tests, **in
      no-LLM mode** (`python3 -m unittest discover -s tests`).
- [ ] The golden AlexSEG end-to-end scenario extended to cover the new capability
      (OI doc §8.1) and passing.
- [ ] No un-cited output element introduced (invariant suite passes).
- [ ] Docs updated: OI doc status markers, `design-decision-log.md` (new decisions or
      status changes), README demo command.
- [ ] Committed + pushed to `claude/gas-scheduler-training-wfuggt`; store rebuilds
      from scratch (`python3 -m nge.load`) without error.

---

## Epic OI-1 — Event Foundation ✅ *(delivered; see commit log)*
*Goal: notices become first-class operational events with status semantics.
Design: OI doc §4.4–4.5 (events), §0.1 (corpus rationale). Depends: none.*

### Issue OI-1.1 — Notice fixture corpus (S) ✅
~~Hand-label ~8–12 notices as extraction gold~~ **Honesty correction applied during
implementation:** `CGT Notices.pdf` is an *index* — headers + subjects, no bodies
(only 26092015/AlexSEG has a full body). Index fixtures therefore grow the **event
derivation gold set**, not the extraction gold set. Delivered: PDFs landed to
`data/raw/cgt/2026-07-04/` with sidecars; 14 index records in
`data/fixtures/notices/cgt_notice_index_2026-06.json` spanning Maintenance /
Capacity Constraint / Force Majeure, a 3-notice `UPDATE:`→`COMPLETED:` chain, a
3-notice FM chain, a `REVISED` supersession pair, and two windowed BannSEG jobs;
hand-derived event gold in `...expected_events.json`.

### Issue OI-1.2 — `operational_event` table + derivation (M) ✅
Delivered: `operational_event` in schema (implementation refinement vs. OI doc §4.5:
the stored column is time-INdependent `lifecycle_status` — posted/updated/completed/
superseded — and the operational status a scheduler sees is the pure function
`nge.events.status_at(window, lifecycle, as_of)`, keeping derivation deterministic
and goldens time-stable); `nge/events.py` chains notices by *facility phrase* (not
SEG code — Banner and New Albany are both BannSEG but are separate jobs), REVISED
creates a superseding event, COMPLETED pins valid_to, windows from fact(0.97) >
subject(0.85) > effective_date(0.60) with source recorded. 15 notices → 11 events,
derivation matches the hand-derived gold 11/11; idempotent; wired into `nge.load`.
The gold caught one real bug pre-commit (an FM UPDATE's effective date must never
pin an open event's end date) — the eval discipline paying for itself.

### Issue OI-1.3 — Event golden scenario (S) ✅
AlexSEG ⇒ maintenance event, window 2026-07-08→10 (window_source=fact, from the
extracted gas days), seg ALEXDRIA; `status_at` asserts planned@Jul-05 /
active@Jul-09 / completed@Jul-11, open-ended FM stays active, superseded wins.

**Milestone DoD: MET** — 11 events (≥8 ✓); 3 event types ✓; 1 supersession chain ✓;
2 multi-notice chains ✓; tests 41/41 green in no-LLM mode ✓; store rebuilds ✓.

---

## Epic OI-2 — Relationship Graph ✅ *(delivered: commit `a86da78`)*
*Goal: first-class typed graph projection — THE foundational model the reasoning
engine operates on. Design: OI doc §3. Depends: none.*

### Issue OI-2.1 — `nge/graph.py` projection (M) ✅
Delivered with three scheduler-thinking upgrades challenged into the design before
coding: (1) interconnect edges carry **flow direction** from Dir Flo codes —
explanations say "receives from"/"delivers to", not "connected to"; (2)
**active_only** traversal default with retired nodes flagged `[RETIRED]` (the
SESH→4208 staleness is enforced behavior now); (3) **lead edges** (CID-only
counterparties, 0.6) visible in neighbors() but excluded from recommended paths
(paths default min_conf=0.7, deliberately above the lead tier). Reconciliation +
known-path goldens all pass; ~16ms build.

### Issue OI-2.2 — `market_hub` / `hub_member` seed tables (S) ✅
HENRY (Sabine 11202 + CGT 519 @0.95) and PERRYVILLE (4235 @0.9, 5112 @0.85,
4209 @0.7) with per-row evidence notes; FK to `point` makes a typo'd membership
fail loudly at load. Seed rows pinned by tests; `hub:HENRY` 1 hop from CGT 519.

### Issue OI-2.3 — Path/subgraph APIs + CLI (M) ✅
`paths/neighbors/subgraph/stats/to_json/to_dot` + `PathResult.explain()` (hop-by-hop,
cited, flow-worded, confidence = min of edges); `python3 -m nge.graphq
stats|neighbors|paths|explain`. Property tests: min-composition, no confidence
inflation with hops, hop bounds, build determinism.

### Issue OI-2.4 — Refactor `nge/reach.py` onto the graph (S) ✅
Recursive CTE replaced by a faithful walk over DECLARED edges only (synthetic
traversal mirrors never used — every hop keeps its TSP-posting citation). Output
proven line-set-identical to the pre-refactor golden; golden then re-pinned because
the old CTE's tie-ordering (4208R before 4208D) was DuckDB-nondeterministic and the
graph version sorts deterministically. All prior string assertions untouched.

**Milestone DoD: MET** — no networkx in core ✓; build ~16ms ✓; 14 new tests,
31/31 at commit ✓.

---

## Epic OI-3 — Constraint Propagation ✅ *(delivered; see commit log)*
*Goal: events → severity-graded, direction-aware, cited downstream risks +
investigations. Design: OI doc §4. Depends: OI-1, OI-2.*

### Issue OI-3.1 — Severity model (S) ✅
`nge/severity.py`: one constants block of reviewable hypotheses (quant bands
5%/15%, service floors, FM/OFO ⇒ critical, mapped-maintenance ⇒ watch floor,
1-band hop decay), pure functions returning (severity, components) — no score
without its reasons. Band-edge units pass; AlexSEG calibration case verified:
13.4% cut (watch) + 'Primary Firm' language ⇒ **action**. UNVERIFIED cap:
critical requires an FM/OFO event type or a human-verified fact — a
hallucinated Dth value can never scream CRITICAL on its own (tested both ways).

### Issue OI-3.2 — `event_impact` + propagation engine (L) ✅
`nge/propagate.py` materializes 39 impact rows from the 11-event corpus.
Direction rule implemented and documented: a directional cut shorts what the
pipe DELIVERS — it propagates across 'out'/'both' edges and does NOT cross a
pure-receipt edge (the counterparty's injection-side exposure is below
public-data resolution; the point's own on-segment row still surfaces it).
AlexSEG golden: 14 on-segment + Perryville `storage_service_at_risk` +
SESH downstream (decayed action→watch) + contract row whose investigation cites
the Egan alternate (roundtrip 1.0) and two SESH receipt leads. 4208R/4208D
direction test passes. Unmapped Corinth FM degrades honestly to a
pipeline-scoped critical @ conf 0.5 with a fix-it instruction. Idempotent
(FK from event_impact→operational_event makes rebuild ordering law).

### Issue OI-3.3 — Impact Engine formalization (M) ✅
`nge/impact.py` `assess()` → typed `ImpactAssessment` with desk-style subject
resolution (event name, SEG idiom via segment_asset_map, asset_key), as-of
status, severity WITH components, UNVERIFIED-flagged facts, impacts grouped by
reason, min-chain confidence roll-up, full citation list; `render()` is the
scheduler briefing. CLI `python3 -m nge.impact --asset AlexSEG --as-of
2026-07-08`. `nge.reach` docstring now marks it superseded (kept as the pinned
Era-1 golden until OI-7).

**Milestone DoD: MET** — citation-integrity test resolves every typed citation
(evt:/fact:/point:/ic:/segmap:/holding:/pipeline:) against its table (>50
checked); impact confidence never exceeds its event's (property); 17 new tests,
58/58 green in no-LLM mode; store rebuilds from scratch.

---

## Epic OI-4 — Operational Timeline ✅ *(delivered; see commit log)*
*Goal: chronological, bitemporal operational view. Design: OI doc §5. Depends: OI-1
(richer with OI-3).*

### Issue OI-4.1 — Timeline query + CLI (M) ✅
`nge/timeline.py`: window-overlap (open-ended events stay open; unknown windows
SHOWN — hiding what we can't date would be a silent miss), pipe/asset/min-severity
filters, hop-0 severity joined from event_impact, supersession display with the
superseding uid. **`as_known` is real bitemporal reconstruction, not a filter:**
the notice subset (post_dt ≤ as_known) is re-folded through the SAME pure chain
logic derive_events uses — `events.fold_notices()`/`finalize_chains()` were
extracted for exactly this (behavior-preserving refactor, all prior tests
untouched). East Lateral viewed on June 25 shows lifecycle 'updated', end June 25,
2 sources — because the COMPLETED notice didn't exist yet. `brief_run` bookkeeping
table + `record_brief_run`/`last_brief_run` (writes only via the recorder; timeline
reads never mutate). CLI `python3 -m nge.timeline --from --to [--pipe] [--asset]
[--min-severity] [--as-of] [--as-known]`.

### Issue OI-4.2 — Status-at-date correctness (S) ✅
AlexSEG flips planned→active at exactly 2026-07-08 and active→completed after
2026-07-10 (boundary-exact tests); open-ended Corinth stays ACTIVE arbitrarily far
out; superseded wins over window status.

**Milestone DoD: MET** — bitemporal golden passes (midnight-July-2 view excludes the
Jul 3–6 posting posted 07:04 that morning); as-known supersession window test
(original not-yet-superseded at 07:30, superseded at 08:00); as-known never mutates
the store (tested); 14 new tests, 72/72 green; README shows a real output sample.

---

## Epic OI-5 — Morning Brief ✅ *(delivered; see commit log)*
*Goal: ranked, cited, one-page daily triage. Design: OI doc §6. Depends: OI-3, OI-4.*

### Issue OI-5.1 — Assembly + explainable ranking (M) ✅
`nge/brief.py`: assembly = non-superseded events that are ACTIVE on the as-of gas
day, PLANNED within a 7-day look-ahead, or freshly posted since a prior
`brief_run` — deliberately, the FIRST brief is NOT a dump of history (novelty adds
to assembly only when there's a real prior run; operational relevance is the
whole filter otherwise). Score = `severity_weight × exposure_factor × novelty ×
confidence` in one reviewable constants block; every component printed by
`--explain` (no score without its reasons). `exposure_factor` is banded on BP firm
Dth/d at affected points (AlexSEG's 27,000 Dth/d BP contract is the only corpus
exposure — it lifts AlexSEG's factor to 1.25 while unexposed items stay 1.0).
Low-confidence *critical* items are floored in with an "⚠️ unconfirmed — verify
first" tag (Corinth FM, conf 0.5). **Acceptance met:** ranking determinism +
component-product property + two novelty demotion tests (whole-set demotion and
post_dt-partial) all green.

### Issue OI-5.2 — Markdown template renderer (S) ✅
Sections Critical → Action → Watch → FYI → Data-quality, worst-first. Byte-stable:
`generated_at` is injectable so the golden pins a fixed timestamp.
**Acceptance met:** `tests/golden/brief_2026-07-05.md` committed; the byte-stability
test fails loudly on any drift; regenerating twice = zero diff.

### Issue OI-5.3 — Citation gate + optional LLM polish (M) ✅
`nge/citegate.py` — the §1.2 verifier, standalone and LLM-free (reusable by OI-6
narration): every sentence must cite ≥1 uid from the input bundle; uncited
sentences are stripped and counted; a single unknown uid fails the WHOLE narration.
The brief's polish seam (`generate(..., narrator=…)`) runs narrator prose through
the gate and adopts it only when clean, else the template ships unchanged.
**Acceptance met:** planted-violation tests (strip+count, unknown-uid full
failure, clean-prose passthrough); brief-level tests for polished-adoption,
unknown-uid fallback, and uncited-strip; `--no-llm`/template mode carries zero LLM
artifacts (no `## Summary`, `mode == "template"`).

**Milestone DoD: MET** — golden brief renders the SESH-4208 staleness under
Data-quality notes (both SESH points 83004/83104 → retired CGT 4208, one merged
note cited to both interconnects + the retired point), proving the section is
load-bearing; 25 new tests (6 citegate + 19 brief), 97/97 green in no-LLM mode;
store rebuilds from scratch.

---

## Epic OI-6 — LLM Client + NL Query
*Goal: English in, cited answers out; provider-agnostic LLM integration. Design: OI
doc §7 + §1.5. Depends: OI-2/3/4 executors (OI-5 for brief-polish reuse).*

### Issue OI-6.1 — `nge/llm.py` provider-agnostic client (S)
DDL-004 honored: env key, configurable model id, single `complete()` surface, no SDK
types past the boundary; clean absent-key behavior.
**Acceptance:** import without key works; LLM tests skip cleanly (HAS_KEY pattern).

### Issue OI-6.2 — Intent registry + executors (M)
8 intents per §7.3; 2 honest stubs (capacity_at_point, storage_status) that state the
data gap + EBB check instructions.
**Acceptance:** every intent has ≥2 gold example questions + executor test; param
fuzz never reaches SQL.

### Issue OI-6.3 — Routers + renderer + CLI (M)
LLM router (tool-use, must pick registered intent or out_of_scope) + fallback
keyword router; routing-vs-answer confidence kept separate; `python3 -m nge.ask`.
**Acceptance:** ~20-question gold set passes on fallback router in CI; refusal golden;
with key, LLM router meets ≥ fallback accuracy on the same set.

**Milestone DoD extras:** "How does the AlexSEG maintenance affect SESH?" answered
correctly via **both** routers with citations.

---

## Epic OI-7 — API Facade + Consolidation
*Goal: one stable in-process contract; the layer is "done" as a library. Design: OI
doc §1.4, DDL-020. Depends: all.*

### Issue OI-7.1 — `nge/api.py` typed facade (M)
One entry per capability, uniform (as_of, as_known) handling, request/response
dataclasses re-exported from capability modules.
**Acceptance:** facade-only smoke test exercises all six capabilities; CLIs re-wired
through it (thin).

### Issue OI-7.2 — Documentation consolidation (S)
`docs/architecture.md` reasoning-layer section finalized with as-built references;
OI doc status markers flipped; README quickstart shows the six commands; deprecated
`nge.reach` alias removed.
**Acceptance:** zero dangling doc links; DDL log statuses current.

**Milestone DoD extras:** full golden AlexSEG end-to-end scenario (§8.1) passes
through the facade alone.

---

## Era 3 horizon (not committed — recorded so Era 2 doesn't over-build)

Scheduled brief delivery (trigger + notifier) · OAC/storage-balance ingestion
(activates quantitative propagation + the two stub intents) · more counterparty
point catalogs (shrink `resolved_cid_only`) · multi-turn copilot over the intent
registry · minimal operational workspace UI over `nge/api.py` + `Graph.to_json()`
(DDL-005 finally retired) · LLM extractor vs. regex baseline on the grown gold set.
