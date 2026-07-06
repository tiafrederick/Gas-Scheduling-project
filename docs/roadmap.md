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

## Epic OI-6 — LLM Client + NL Query ✅ *(delivered; see commit log)*
*Goal: English in, cited answers out; provider-agnostic LLM integration. Design: OI
doc §7 + §1.5. Depends: OI-2/3/4 executors (OI-5 for brief-polish reuse).*

### Issue OI-6.1 — `nge/llm.py` provider-agnostic client (S) ✅
DDL-004 honored: `LLMClient.complete(system, messages, tools?, tool_choice?)` returns
a plain `LLMResponse` (no `anthropic` types cross the boundary); model id from
`NGE_LLM_MODEL` (defaults to the project's Claude Fable 5 build, overridable). The
`anthropic` import is lazy so `import nge.llm` always succeeds; `has_key()` /
`available()` gate the LLM path. Current-model-safe: sends no `thinking` /
`temperature` / prefill (all 400 on Fable 5 / Opus 4.8 / Sonnet 5), and surfaces
`stop_reason == "refusal"` as `refused=True` (checked before any content read).
**Acceptance met:** imports with no SDK and no key; the real-key router test skips
cleanly (HAS_KEY pattern).

### Issue OI-6.2 — Intent registry + executors (M) ✅
`nge/intents.py`: 8 typed intents (asset_impact, events_in_window, path_between,
point_lookup, contract_exposure, interconnect_partners + the two stubs), each a thin
cited wrapper over the OI-2..OI-5 APIs; `capacity_at_point` / `storage_status` ship as
**honest stubs** that name the data gap (OAC screen / shipper-login balance) and the
exact EBB check, at answer_confidence 0.0. The module imports with no duckdb (the
router reads `REGISTRY` metadata without a DB; heavy imports are lazy).
**Acceptance met:** each intent has ≥2 gold questions + an executor test; the param
security test proves injection strings (`'; DROP TABLE point; --`) and wrong-type
params never reach SQL and never raise — every query is parameterized.

### Issue OI-6.3 — Routers + renderer + CLI (M) ✅
`nge/ask.py`: a keyword **fallback router** (deterministic, CI-tested) and an **LLM
router** (one forced `route` tool-use call; must pick a registered intent or
`out_of_scope`; degrades to the fallback on any refusal/error). Routing confidence and
answer confidence are separate `NLAnswer` fields, never blended (§7.6). Out-of-scope ⇒
an explicit refusal listing what CAN be asked. CLI `python3 -m nge.ask "<question>"
[--as-of] [--router auto|llm|fallback]`.
**Acceptance met:** the 20-question gold set passes on the fallback router; refusal
golden (basis-forecast ⇒ out_of_scope + capability list); the LLM tool-call parsing is
exercised deterministically with a fake client (no key), and the real-key accuracy
test is present + skipped in no-key CI.

**Milestone DoD: MET** — "How does the AlexSEG maintenance affect SESH?" routes to
`asset_impact` and answers with the event citation + SESH exposure via **both** the
fallback router (tested) and the LLM router (fake-client tested; real-key path
skipped); 25 new tests, 122/122 green in no-LLM mode; store rebuilds from scratch.

---

## Epic OI-7 — API Facade + Consolidation ✅ *(delivered; see commit log)*
*Goal: one stable in-process contract; the layer is "done" as a library. Design: OI
doc §1.4, DDL-020. Depends: all.*

### Issue OI-7.1 — `nge/api.py` typed facade (M) ✅
`nge/api.py`: an `Engine` session object owns the DuckDB connection (read-only by
default — one place enforcing the invariant), caches the graph build, and exposes ONE
method per capability, each returning a typed `*Response` with a uniform envelope
(`capability`, `as_of`, `as_known`, `error`) whose `render()` delegates to the wrapped
capability object — so the facade changes NO externally-observable output (goldens
unmoved). Uniform bitemporal contract: `as_known` is threaded where a capability
reconstructs history (timeline) and rejected with `AsKnownUnsupported` where it
doesn't (impact/brief/ask) — never silently ignored. Graph methods are atemporal
(`as_of=None`). The five CLIs are now thin wrappers: parse args → `with Engine(db) as
e: print(e.<cap>(...).render())` (dead `duckdb`/`record_brief_run` imports removed).
**Acceptance met:** `tests/test_api.py` exercises all six capabilities through the
facade alone; an architectural-guard test asserts each CLI's `main()` uses `Engine`
and opens no connection of its own.

### Issue OI-7.2 — Documentation consolidation (S) ✅
`docs/architecture.md` gains an as-built reasoning-layer section; DDL-020 carries an
as-implemented addendum; README quickstart shows the facade + the six commands.
**Deviation from the original issue (kept `nge.reach`):** the deprecated alias has
internal callers — two test goldens (`tests/golden/reach_alexseg.txt`) that are live
regression anchors — so per the maintainability directive it is *excluded from the
facade and marked internal* but NOT deleted (removing it changes coverage for no
benefit). Tracked as debt in `docs/design-decision-log.md` open items.

**Milestone DoD: MET** — the full §8.1 AlexSEG end-to-end scenario (impact → timeline →
NL query → graph roundtrip → brief) passes through the facade **alone**; the bitemporal
golden and the brief golden both pass routed through `Engine`; a deliberate,
documented behavior change (graceful bad-input handling on graph methods + a
caller-neutral no-path hint) replaces a pre-existing uncaught `KeyError`; 16 new tests,
138/138 green in no-LLM mode; store rebuilds from scratch.

---

## Era 3 — Application Boundary + Operations Workspace

*Design authority: [`era3-api-boundary.md`](./era3-api-boundary.md) (the Operation
Contract over `nge/api.py::Engine`). Objective: minimize technical risk while
delivering user value as early as possible. The Engine stays the single source of
reasoning; transports are thin adapters with no business logic.*

### Phase 1 — Boundary Hardening

Harden `nge/api.py` into a versioned, structured, self-describing contract. All
read-only, in-process — no transport, no frontend. Each task is classified for the
**first user-facing release** (the first Operations Workspace screen — the Morning
Brief): **[C]** critical before it · **[P]** postpone until after it · **[L]** long-term.

| Task | Purpose | Depends | Cx | Risk | Class |
|---|---|---|---|---|---|
| P1.1 Operation registry (`{name, params_schema, kind, temporal, honors_as_known}`) | Single source of truth; anti-drift; feeds validation/`describe`/transports | — | L | Low | **[C]** minimal (brief entry); registry-driven *generation* is [P] |
| P1.2 `to_dict()` + shared value objects (`Citation`, `Confidence`, `Claim`, `Fact`) | The structured contract itself | P1.1 | M | Med (expose fields some payloads only render) | **[C]** for `Citation`+`Confidence`+brief payload; `Claim`/`Fact` [P] with impact |
| P1.3 Error-as-data taxonomy (closed code set) | Transport-neutral failures; no leaked exceptions | P1.1 | L | Low | **[P]** — minimal error shape in the envelope now; full taxonomy with 2nd op |
| P1.4 Envelope (`contract_version`, `query`, `dataset.snapshot_id` field, `warnings[]`, read/`brief.record` split) | The stable wrapper; the one write made explicit | P1.2, P1.3 | L | Low | **[C]** (shape) — `snapshot_id` populated simply; durability machinery [L] |
| P1.5 Boundary input validation (schema-driven) | Untrusted params can't reach a `TypeError`/SQL | P1.1 | L | Low | **[C]** minimal (brief params); full schema fuzz [P] |
| P1.6 `describe` operation | Runtime capability discovery | P1.1 | L | Low | **[P]** — the first screen is hardcoded; needed at MCP / a generic UI |
| P1.7 Neutralize `nge.reach` on the public surface | Don't publish a deprecated path | P1.1 | L | Low | **[P]** — not reachable from a brief-only endpoint; excise with the general transport |

**Revised Phase 1 critical path (minimum for the first screen, long-term shape preserved):**
1. Stable serialized shapes for `Citation` + `Confidence` (the expensive-to-change-later atoms).
2. `to_dict()` for the brief payload (Brief · sections · BriefItem · ScoreComponents · DataQualityNote) composing them.
3. The versioned envelope wrapping that payload (`contract_version`, `query{as_of,as_known}`, `dataset.snapshot_id`, `result`, `warnings[]`, minimal `error`).
4. A one-entry operation registry (`brief`) + minimal `as_of`/param validation.

Everything else in Phase 1 (full error taxonomy, `describe`, all-capability serialization, `Claim`/`Fact`, reach excision, the write path) is **[P]** — see the deferral notes in the Era-3 review appended below the roadmap discussion.

### Phase 2 — First Transport: **MCP**

*Recommended because the near-term consumer is the scheduler using Claude (Fable 5) as a
copilot — the original vision — and MCP delivers cited, deterministic Engine answers with
zero frontend. HTTP is transport #2 (Phase 3). If the workspace screen is the sole
near-term product, MCP may ship as a fast-follow rather than before Phase 3.*

| Task | Purpose | Depends | Cx | Risk | User value |
|---|---|---|---|---|---|
| P2.1 MCP server adapter (one tool per operation, generated from the registry) | Expose the Engine to the copilot | Phase 1 | M | Med (MCP wiring; spec churn — mitigated by thin generated adapter) | **High** — English Q&A with cited answers, no UI |
| P2.2 Conformance parity test (in-proc vs MCP ⇒ identical result) | Lock "adapters are thin" | P2.1 | L | Low | Trust |

### Phase 3 — Operations Workspace (first Fable 5 screen = Morning Brief)

*The brief is the highest-value daily artifact, already fully structured and byte-stable,
and needs no new reasoning — only serialization (P1) + a transport.*

| Task | Purpose | Depends | Cx | Risk | User value |
|---|---|---|---|---|---|
| P3.1 HTTP/JSON adapter (thin `GET`/`POST /v1/operations/{name}`) | The transport a browser speaks | Phase 1 | M | Med (first web surface; keep thin) | Enables UI |
| P3.2 Concurrency model (read-only connection pool; serialized writer) | Safe multi-request serving | P3.1 | M | **Med–High** (DuckDB thread-safety) | Correctness under load |
| P3.3 Serve `brief` + `describe`; envelope drives the screen | The screen's data | P3.1, P1.6 | L | Low | — |
| P3.4 First Fable 5 screen (render the brief envelope) | The scheduler's workspace | P3.1–3.3 | M | Med (frontend over a stable contract) | **High** — the daily brief as a screen |

### Phase 4 — Intentionally postponed

`ask`-structured executors (until an in-workspace NL screen) · citation durability
across rebuilds + `/resolve` (until a caching/click-through client) · auth/multi-user ·
`retriable`/`details` errors · `schema_hash` + CI contract-diff · deprecation-warning
lifecycle · `impact`/`brief`-as-known reconstruction · real `capacity`/`storage`
capabilities (OAC/storage-balance data) · scheduled brief delivery · `nge.reach`
retirement (tracked debt) · gRPC/GraphQL (likely never).

**Sequencing rule (unchanged from Era 2):** a phase starts only when its dependencies'
exit criteria are met; no un-cited output; every transport is a thin, conformance-tested
adapter over the one contract.
