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

## Epic OI-3 — Constraint Propagation
*Goal: events → severity-graded, direction-aware, cited downstream risks +
investigations. Design: OI doc §4. Depends: OI-1, OI-2.*

### Issue OI-3.1 — Severity model (S)
Quantitative bands + qualitative floors + max() composite + per-hop decay (§4.3), one
constants block.
**Acceptance:** band-edge unit tests incl. AlexSEG 13.4%+PrimaryFirm ⇒ `action`.

### Issue OI-3.2 — `event_impact` + propagation engine (L)
`nge/propagate.py` per §4.2/§4.5–4.7: seeded BFS, direction filtering, reason codes,
investigation strings, materialization.
**Acceptance:** AlexSEG golden ⇒ ≥3 investigations citing 4208R/4208D/Egan-alternate;
direction test (backhaul does not propagate along pure-forward delivery edges);
idempotent re-run.

### Issue OI-3.3 — Impact Engine formalization (M)
`nge/impact.py` `assess()` returning `ImpactAssessment` (§2.3), consuming
event_impact + live graph; `nge.reach` becomes deprecated alias.
**Acceptance:** golden assessment matches §2.7; UNVERIFIED severity cap enforced.

**Milestone DoD extras:** every `event_impact` row's `citations` chain resolves to
real uids (integrity test).

---

## Epic OI-4 — Operational Timeline
*Goal: chronological, bitemporal operational view. Design: OI doc §5. Depends: OI-1
(richer with OI-3).*

### Issue OI-4.1 — Timeline query + CLI (M)
`nge/timeline.py` per §5.5 incl. `as_known`; `brief_run` bookkeeping table.
**Acceptance:** ordering/window/filter tests; bitemporal golden (July-2 view excludes
July-3 posting); supersession display test.

### Issue OI-4.2 — Status-at-date correctness (S)
`status_at_as_of` computed per event window vs. explicit completion notices.
**Acceptance:** planned→active→completed flips at exact boundary dates.

**Milestone DoD extras:** `python3 -m nge.timeline --from --to` documented in README
with real output sample.

---

## Epic OI-5 — Morning Brief
*Goal: ranked, cited, one-page daily triage. Design: OI doc §6. Depends: OI-3, OI-4.*

### Issue OI-5.1 — Assembly + explainable ranking (M)
Novelty via `brief_run`; score components per §6.2; `--explain`.
**Acceptance:** ranking determinism; component-product property; novelty demotion test.

### Issue OI-5.2 — Markdown template renderer (S)
Sections Critical/Action/Watch/FYI/Data-quality; byte-stable.
**Acceptance:** golden brief file committed; regenerating = zero diff.

### Issue OI-5.3 — Citation gate + optional LLM polish (M)
§1.2 verifier (standalone, LLM-free, tested with planted violations); polish path
behind it; template fallback on any gate failure.
**Acceptance:** planted uncited sentence stripped+counted; unknown uid ⇒ full fallback;
`--no-llm` output has zero LLM artifacts.

**Milestone DoD extras:** golden brief renders the SESH-4208 staleness under
Data-quality notes (proves the section isn't decorative).

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
