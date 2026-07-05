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

## Epic OI-1 — Event Foundation
*Goal: notices become first-class operational events with status semantics.
Design: OI doc §4.4–4.5 (events), §0.1 (corpus rationale). Depends: none.*

### Issue OI-1.1 — Notice fixture corpus (S)
Hand-label ~8–12 notices from `CGT Notices.pdf` (already in the original upload; land
the PDF to `data/raw/cgt/` with sidecar) as gold fixtures using the existing
`*.expected_facts.json` pattern. Must span: Maintenance, Capacity Constraint, Force
Majeure, an `UPDATE:`, a `COMPLETED:`, a `REVISED` supersession pair.
**Acceptance:** fixtures load via `nge.load`; extraction eval runs over all (baseline
scores reported, not required to be 1.0 — that's the point of a broader gold set);
corpus documented in `docs/data-sources.md`.

### Issue OI-1.2 — `operational_event` table + derivation (M)
`schema/canonical.sql` additions per OI doc §4.5; `nge/events.py` `derive_events()`
implementing the status machine (§4.4), idempotent, wired into `nge.load`.
**Acceptance:** every corpus notice yields exactly one event (supersession pairs
linked via `supersedes_event_uid`); status-machine table tests pass for all observed
idioms; re-derivation is a no-op diff.

### Issue OI-1.3 — Event golden scenario (S)
AlexSEG notice ⇒ event `maintenance`, window 2026-07-08→10, correct planned/active/
completed status at three different `as_of` dates.
**Acceptance:** test asserts all three; added to invariant suite.

**Milestone DoD extras:** ≥8 events in store; ≥3 distinct event types; ≥1 supersession
chain.

---

## Epic OI-2 — Relationship Graph
*Goal: first-class typed graph projection. Design: OI doc §3. Depends: none.*

### Issue OI-2.1 — `nge/graph.py` projection (M)
Typed nodes/edges per §3.3, built from canonical tables; stdlib only.
**Acceptance:** reconciliation tests (counts by kind == SQL); known-path goldens
(CGT↔Egan 1.0, CGT↔Sabine 1.0, SESH↔CGT 0.9 preserved).

### Issue OI-2.2 — `market_hub` / `hub_member` seed tables (S)
Schema + curated seed (HENRY ← Sabine 11202 + CGT 519; PERRYVILLE cluster), confidence
+ evidence notes, loaded in `nge.load`.
**Acceptance:** seed rows pinned by tests; `hub:HENRY` reachable in graph.

### Issue OI-2.3 — Path/subgraph APIs + CLI (M)
`paths/neighbors/subgraph/to_json/to_dot`, `min_confidence` filter,
`python3 -m nge.graphq`.
**Acceptance:** property tests (min-composition, hop bounds); `paths(C000307:519,
hub:HENRY)` returns a 1-hop 0.95 path.

### Issue OI-2.4 — Refactor `nge/reach.py` onto the graph (S)
Replace inline recursive CTE with graph traversal; identical report output
(golden-pinned before refactor).
**Acceptance:** existing 17 tests untouched and green; reach output byte-identical.

**Milestone DoD extras:** no networkx in core imports; graph build <1s at current scale.

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
