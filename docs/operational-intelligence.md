# Operational Intelligence Layer — Architecture (Era 2)

> Status: **DESIGN — approved for implementation via the OI-1…OI-7 milestones in
> [`docs/roadmap.md`](./roadmap.md).** No OI code exists yet; this document is the
> contract it will be built against.
>
> Decision records: DDL-014…DDL-020 in
> [`docs/design-decision-log.md`](./design-decision-log.md).

---

## 0. Purpose and era framing

Era 1 (repo phases 0–4, commits `fbea0bc`…`44d7686`) built a **knowledge engine**: it
can ingest public EBB data, normalize it into a canonical bitemporal store, resolve
cross-pipeline identity with confidence tiers, and answer one cited impact question.
Era 2 turns that engine into an **operational reasoning system** — one that helps a
professional scheduler decide *what matters today, why, and what to check next*.

The shift in posture: **no new ingestion breadth unless architecturally required.**
Era 2 spends its complexity budget on reasoning over what is already normalized.
(The single justified ingestion increment is scoped in §7: an operational timeline
with one event is untestable, so ~8–12 notices from the already-uploaded
`CGT Notices.pdf` are hand-labeled as fixtures in OI-1 — fixture labeling that also
grows the extraction gold set, not new parser infrastructure.)

### 0.1 Readiness assessment

| OI layer needs | Already built (Era 1) | Gap |
|---|---|---|
| Normalized entities w/ stable keys | `pipeline`, `point (tsp_ferc_cid, loc)`, `contract_holding/_point` — `schema/canonical.sql` | none |
| Cross-pipeline edges w/ trust levels | `interconnect` + 6-tier confidence (`nge/resolve.py`); 2 proven 1.0 round-trips; SESH→CGT staleness surfaced at 0.9 | none |
| Asset↔topology mapping | `segment_asset_map` (DDL-013), confidence+evidence per row | coverage: 4 CGT segments only |
| Extracted quantitative facts | `capacity_impact_fact` w/ provenance, confidence, `verified_by`, gas-day validity, direction | corpus: 1 notice |
| Impact traversal | `nge/reach.py` — asset→segment→points→recursive CTE→BP exposure, fully cited | ad-hoc; not a reusable graph API |
| Anti-hallucination pattern | extraction **span gate** (`nge/extract/eval.py`, tested to *fail* things) | must be generalized to generated text |
| Event semantics | `notice` table has lifecycle fields (Initiate/Update/Complete, prior_notice_id) | no derived event model, no status machine |
| LLM integration | none (deliberate; DDL-004 interface designed) | `nge/llm.py` client; graceful no-key mode |
| Service surface | CLI modules (`nge.load`, `nge.reach`, `nge.extract.eval`) | no typed facade |

**Verdict: the foundation is ready.** Nothing in Era 1 must be reworked; every OI
capability composes existing tables and patterns. The two real risks are *data
coverage* (one notice; four mapped segments), addressed by OI-1 fixtures and an
explicit coverage register — not by new ingestion machinery.

---

## 1. Cross-cutting principles

These bind all six capabilities. They are the enforcement of DDL-014 and the reason
this layer is trustworthy enough for a professional desk.

### 1.1 Deterministic core, LLM shell (DDL-014)

All **selection** logic — which assets, points, edges, contracts, and events are
affected, and with what severity and confidence — is deterministic SQL/graph traversal
over the canonical store. It is auditable, unit-testable, and cited. The LLM appears in
exactly three places, all replaceable and all optional:

1. **Extraction** (notice body → typed facts; designed in Era 1, `docs/extraction-schema.md`).
2. **Narration** (deterministically-assembled input bundle → prose), gated per §1.2.
3. **Translation** (natural-language question → whitelisted intent + params, §6).

Every capability must produce correct — if plainer — output with **no API key**.
The test suite runs in no-LLM mode by default; LLM-path tests are additive.

### 1.2 The citation gate (generalizing the span gate)

Era 1's extraction eval rejects any fact whose `source_span` is not a verbatim
substring of its source ("an extractor may be wrong, but it may not invent text").
Era 2 generalizes this to generation:

- Every narration call receives an **input bundle**: the typed objects (events, facts,
  impacts, risks) it may talk about, each carrying a uid.
- Generated prose must tag each sentence with the uid(s) it derives from
  (`…curtailments possible at 4208R [evt_ab12, imp_9f3c].`).
- A deterministic **verifier** (no LLM) checks every cited uid exists in the bundle and
  every sentence carries ≥1 citation. Uncited sentences are stripped and counted;
  unknown uids fail the whole narration, falling back to template output.
- The verifier is itself tested with planted violations (same discipline as
  `tests/test_extraction_eval.py::TestScorerCatchesBadExtractions`).

### 1.3 Confidence algebra

One algebra everywhere, so numbers mean the same thing in every capability:

- **Sources of confidence:** interconnect edge tier (1.0/0.9/0.6/0.4/0.0),
  `segment_asset_map.confidence` (0.7–0.9), `capacity_impact_fact.confidence`
  (extraction), curated-seed tables (`market_hub`/`hub_member`, like DDL-013).
- **Composition = `min()`** along any chain (weakest link). A scheduler treats a path
  as exactly as trustworthy as its sketchiest hop; a product would punish long paths
  even when every hop is solid, and confidences here are trust grades, not
  independent probabilities. Hop count is always reported alongside.
- **Invariant (property-tested): confidence never increases along a derivation chain.**
- **Presentation bands:** ≥0.9 *solid*, 0.6–0.89 *probable — verify*, <0.6
  *lead only — investigate before acting*. `verified_by IS NULL` extraction facts are
  additionally flagged `UNVERIFIED` regardless of numeric confidence (existing
  `nge/reach.py` behavior, kept).

### 1.4 Bitemporal query contract

Every OI API takes `as_of` (gas day — what is *true* then) and optional `as_known`
(timestamp — what we *knew* then, from `system_recorded_at`). Default: today / now.
This is the payoff of the Era-1 bitemporal design: "show me Tuesday's brief as it
would have looked Tuesday 07:00" is a first-class query, which is also how the
system's own past recommendations get audited.

### 1.5 No-LLM degradation table

| Capability | With `ANTHROPIC_API_KEY` | Without |
|---|---|---|
| Impact Engine | identical (deterministic) + optional narrative | identical, template narrative |
| Relationship Graph | identical (deterministic) | identical |
| Constraint Propagation | identical (deterministic) | identical |
| Timeline | identical (deterministic) | identical |
| Morning Brief | template + LLM polish (citation-gated) | template only (byte-stable, golden-tested) |
| NL Query | LLM intent routing (broader phrasings) | keyword/pattern router (same intents, narrower phrasing) |

---

## 2. Capability: Operational Impact Engine

### 2.1 Business value
When a critical notice posts at 07:04, the scheduler's first questions are: *which of
my points does this touch, is my firm service exposed, and how bad is it?* Today that
means cross-referencing the notice against tariff knowledge, point lists, and memory —
under Timely-cycle deadline pressure (nominations due ~13:00 CT). The Impact Engine
answers in seconds, with citations, so the scheduler spends the deadline window
deciding, not reconstructing.

### 2.2 Architecture
Formalizes `nge/reach.py` into `nge/impact.py`. Pipeline:
`event (or asset name) → segment_asset_map → segment points → graph edges (via §3
Relationship Graph, replacing the inline recursive CTE) → counterparty pipes →
contract exposure → severity (§4 model) → investigations → ImpactAssessment`.
Pure function of (store, event_uid|asset, as_of); no hidden state.

### 2.3 Data structures
```python
@dataclass ImpactAssessment:
    subject: EventRef | AssetRef        # what was assessed
    facts: list[FactRef]                # capacity_impact_fact uids + values + UNVERIFIED flags
    segments: list[SegmentMapRef]       # mapping rows used, each w/ confidence + evidence note
    affected_points: list[PointRef]     # on-segment points, incl. inactive ones flagged
    crossings: list[EdgeRef]            # interconnect edges crossed, w/ tier + confidence
    counterparties: list[PipelineRef]   # reached pipes, portfolio-flagged
    exposure: list[ContractExposure]    # holding, MDQ, affected contract points
    severity: Severity                  # §4.3 bands, with score components
    investigations: list[Investigation] # reason_code + human instruction + citations
    confidence: float                   # min() roll-up
    citations: list[str]                # every uid referenced anywhere above
```

### 2.4 Tables / entities
None new (consumes `event_impact` from §4 when assessing an event; computes live when
assessing a raw asset name). Deliberate: the Impact Engine is a *view* over the
propagation engine's materialization plus live graph traversal.

### 2.5 APIs
`nge/impact.py: assess(subject: str, as_of: date, as_known: datetime|None, max_hops=2)
-> ImpactAssessment` · CLI `python3 -m nge.impact --asset AlexSEG --as-of 2026-07-08`
(replaces `nge.reach`, which becomes a thin deprecated alias for one milestone, then
is removed).

### 2.6 Confidence scoring
Per-element: mapping conf × edge tier × fact conf, composed by `min()` per §1.3.
Roll-up = min over elements actually cited in the assessment. UNVERIFIED facts never
raise severity above *action* on their own (a human-verified fact is required for
*critical* unless the event type itself is FM/OFO).

### 2.7 Testing
- **Golden scenario (the integration test of record):** AlexSEG notice 26092015 →
  assessment cites facts `cd6e3f9e…`/`64b2aa51…`, maps ALEXDRIA at 0.9, lists 14
  points incl. `4208D/4208R`, crosses to SESH at 0.9, surfaces contract `840245-R1`
  (27,000 Dth/d) and ≥3 investigations.
- Invariants: no un-cited element; confidence non-increasing; inactive points flagged
  not hidden; `max_hops` respected.
- Negative: unknown asset → explicit "no mapping" degradation (already tested in
  `test_unknown_asset_fails_soft`, kept).

### 2.8 Risks & assumptions
Segment-map coverage is the binding constraint (4 CGT segments; unmapped assets
degrade to pipeline-level — visible, not wrong). No point-level OAC data yet, so
exposure is qualitative at points (extension §2.9). Assumes notices name assets using
idioms already observed (AlexSEG/BannSEG…); new idioms fall back gracefully.

### 2.9 Extensibility
When `operational_capacity_fact` gains real OAC rows (deferred ingestion, slot already
in schema), the engine upgrades from "firm exposure exists" to "point X shows Y Dth
headroom at TIMELY" with zero interface change. Multi-asset events (a notice
constraining two segments) are a list at the `segments` field, already typed for it.

---

## 3. Capability: Pipeline Relationship Graph

### 3.1 Business value
The scheduler's mental map — *what connects to what, through where* — is the asset
that takes years to build. The graph makes it explicit, queryable, and confidence-
annotated: alternate receipt paths when a primary is constrained, which storage can
serve which market, what sits between CGT and a Henry Hub sale. For a 5-month
scheduler this is also the primary *learning* instrument (DDL-005's "thinking
workspace" without a UI).

### 3.2 Architecture
`nge/graph.py` — an in-memory projection built on demand from canonical tables
(DDL-015; graph is never a store, reaffirming DDL-001/010). Stdlib adjacency dict;
no networkx in core (optional export adapter only). Build time at current scale
(249 points, 249 edges) is trivially fast; rebuilt per process, cached per connection.

### 3.3 Data structures
```python
@dataclass GraphNode: uid: str; kind: NodeKind; label: str; attrs: dict
# NodeKind = pipeline | point | segment | storage_facility | market_hub
@dataclass GraphEdge: src: str; dst: str; kind: EdgeKind; confidence: float; citation: str
# EdgeKind = interconnect | on_segment | of_pipeline | storage_service | hub_member
@dataclass PathResult: nodes: [...]; edges: [...]; hops: int; confidence: float  # min()
```
Node/edge uids are canonical keys (`C000307:4123`, `seg:C000307:ALEXDRIA`,
`hub:HENRY`) — the graph invents no identity.

### 3.4 Tables / entities
Two new **curated seed tables** (same governance as `segment_asset_map`: confidence +
evidence note per row, never silently assumed):
```sql
market_hub(hub_id PK, name, region, note)                    -- seed: HENRY, PERRYVILLE
hub_member(hub_id, point_uid, confidence, note)              -- e.g. HENRY ← C000830:11202 (0.95),
                                                             --        HENRY ← C000307:519  (0.95)
```
Storage facilities need no new table: `kind=storage_facility` derives from
`point.loc_type_ind='STR'` plus the EGAN/BOBCAT pipelines themselves.

### 3.5 APIs
`build(con, as_of) -> Graph` · `Graph.neighbors(uid, kinds?)` ·
`Graph.paths(a, b, max_hops=4) -> list[PathResult]` (BFS, all shortest + near-shortest)
· `Graph.subgraph(pipeline|segment|hub)` · `Graph.to_json()/to_dot()` (future UI/viz
contract) · CLI `python3 -m nge.graphq paths C000307:519 hub:HENRY`.

### 3.6 Confidence scoring
Edges inherit: interconnect tier; `on_segment`/`of_pipeline` = 1.0 (from the TSP's own
catalog); `hub_member` from seed row. Path = min(edges). Optional
`min_confidence` filter on all traversals (default 0.6 — excludes `declared_external`
leads from pathfinding unless explicitly requested).

### 3.7 Testing
Projection reconciliation (node/edge counts == SQL counts by kind); known-path goldens
(CGT 4123↔Egan 45103 @1.0; CGT 519↔Sabine 11202 @1.0; SESH 83004↔CGT @0.9 — the
staleness case must *stay* 0.9); property tests (path confidence == min of its edges;
paths respect max_hops; undirected symmetry where both directions exist).

### 3.8 Risks & assumptions
Hub membership is curated — wrong curation propagates (mitigated: confidence + note +
tests pin the seed rows). At larger scale (dozens of pipes) rebuild-per-process may
need caching — measured before optimized. Direction semantics on `B` (bidirectional)
points are permissive by design; propagation (§4), not the graph, applies direction
filters.

### 3.9 Extensibility
New node/edge kinds are enum additions (e.g. `lng_terminal`, `pool_point` if pool
points are ever re-included). The `to_json()` payload is the future UI's wire format.
More pipes = more rows, zero code.

---

## 4. Capability: Constraint Propagation Engine

### 4.1 Business value
The senior-scheduler instinct being encoded: *"Alexandria's down for three days —
that's my SESH receipts squeezed, so Coden deliveries need watching, and if I lose
supply there I'm pulling from Egan."* The engine reasons one honest step at a time:
constraint → exposed assets → downstream leads → **what to verify**, never pretending
public data can predict actual scheduled-quantity outcomes.

### 4.2 Architecture
Two stages, both deterministic:
1. **Event derivation** (`nge/events.py`): notices + facts → `operational_event` rows
   via a status machine (§4.4). Runs at load time; rebuildable (DDL-016).
2. **Propagation** (`nge/propagate.py`): event → seeded points (via segment map) →
   direction-aware bounded BFS over the graph (§3) → `event_impact` rows with
   severity, reason codes, investigations. Materialized for timeline/brief speed;
   recomputable at will.

### 4.3 Data structures — severity model
`severity = max(quantitative_band, qualitative_band)`:
- **Quantitative** (needs a capacity fact): cut% = 1 − setting/design →
  <5% `informational` · 5–15% `watch` · >15% `action`. Range facts evaluate at the
  *conservative* end (max cut). AlexSEG: 1−2,325,000/2,683,256 = **13.4% ⇒ watch** on
  numbers alone — a deliberately instructive boundary case, because:
- **Qualitative**: notice affects Secondary Firm ⇒ ≥`watch`; Primary Firm ⇒ ≥`action`
  (AlexSEG names Primary Firm ⇒ final severity **action**); event type force_majeure
  or OFO ⇒ `critical` regardless of numbers.
- Severity **decays one band per hop** away from the constrained asset (an *action* at
  the segment is a *watch* one interconnect away) — encoded, explainable, overridable
  per event type later.

### 4.4 Data structures — event status machine
`planned → active → completed | superseded`, derived from: notice `notice_stat_desc`
(Initiate/Update/Complete), observed CGT subject idioms (`COMPLETED:` / `UPDATE:` /
`REVISED` prefixes), `prior_notice_id` chains, and gas-day window vs. `as_of`
(planned before `valid_from`, active within, completed after unless an explicit
completion notice arrived earlier). Supersession keeps *both* events; the newer
references the older (`supersedes_event_uid`) — the timeline shows the correction
history, which is itself operational signal.

### 4.5 Tables / entities
```sql
operational_event(
  event_uid PK, tsp_ferc_cid, event_type,      -- maintenance|capacity_constraint|force_majeure|ofo|restoration|rate_change|other
  asset_name, seg_cd NULL, status,             -- planned|active|completed|superseded
  valid_from DATE, valid_to DATE,
  severity, supersedes_event_uid NULL,
  source_notice_uids VARCHAR[], confidence DOUBLE, system_recorded_at TIMESTAMP)

event_impact(
  impact_uid PK, event_uid FK, subject_uid,    -- point/interconnect/pipeline/holding uid
  subject_kind, reason_code,                   -- on_constrained_segment|downstream_interconnect|
                                               -- contract_at_affected_point|storage_service_at_risk
  hop_distance INT, severity, confidence DOUBLE,
  investigation VARCHAR,                        -- the human instruction
  citations VARCHAR[], system_recorded_at TIMESTAMP)
```

### 4.6 APIs
`derive_events(con) -> int` (idempotent rebuild) ·
`propagate(con, event_uid, max_hops=2, as_of) -> list[PropagatedRisk]` ·
CLI `python3 -m nge.propagate --event <uid>`.

### 4.7 Confidence scoring
Seed = min(fact conf, segment-map conf). Each hop: ×min(edge tier) — composed via
min(), so a 0.9-mapped asset crossing a 0.9 edge yields 0.9, crossing a 0.6
`resolved_cid_only` lead yields 0.6 (*lead only* band, still surfaced, clearly
labeled). Materialized rows store their chain in `citations` for audit.

### 4.8 Testing
Severity units (band edges: 4.9/5.0/15.0/15.1%; range-fact conservative rule; the
AlexSEG 13.4%+PrimaryFirm ⇒ action composite); status-machine table tests (every
transition, incl. UPDATE and COMPLETED idioms from the OI-1 corpus); supersession
(REVISED chain keeps both, statuses correct); direction filtering (backhaul constraint
does not propagate along pure-delivery forward edges); decay-per-hop; idempotent
re-derivation (run twice ⇒ identical tables).

### 4.9 Risks & assumptions
The public-data ceiling is the defining constraint: **outputs are exposure + leads,
never predicted flows** — framing enforced in type names (`PropagatedRisk`,
`investigation`) and docs. Direction semantics rest on `dir_flo` codes + fact
`direction`; odd cases (bidirectional storage points) default to propagating (false
lead > silent miss, at confidence cost). Status idioms are CGT-observed; other pipes'
idioms get added to the derivation table as their notices arrive (data, not code).

### 4.10 Extensibility
OFO support is an `event_type` + severity rule already reserved (no OFO notice in
corpus yet — flagged, not faked). Quantitative propagation (headroom math) activates
when OAC facts exist. Nomination-cycle awareness (which cycle can still react — e.g.
"posted 07:04, you have until ID1") is an additive field on `Investigation`.

---

## 5. Capability: Operational Timeline

### 5.1 Business value
"What's happening on my pipes this week?" is currently answered by re-reading EBB
lists per pipe. The timeline consolidates all portfolio events into one chronological,
status-aware view — and because it is bitemporal, it also answers "what did we know
Friday?" (the desk-handover and post-mortem question).

### 5.2 Architecture
Thin, deterministic query layer over `operational_event` (+`event_impact` for
enrichment) in `nge/timeline.py`. No new derivation — the propagation engine owns
writes; the timeline owns reads. (Kept separate deliberately: reading history must
never mutate it.)

### 5.3 Data structures
```python
@dataclass TimelineEntry: event: EventRef; status_at_as_of: str; window: (date, date)
                          severity: Severity; top_impacts: list[ImpactRef]  # capped, cited
@dataclass Timeline: entries: [...]; window: (date, date); as_of: date; as_known: datetime
```

### 5.4 Tables / entities
None new (reads §4's tables). One tiny addition: `brief_run(run_at, as_of, window)` —
shared bookkeeping used by the brief's novelty detection (§6), written here because
the timeline is the natural owner of "what was already seen".

### 5.5 APIs
`timeline(con, date_from, date_to, pipelines?, assets?, min_severity?, as_of, as_known?)
-> Timeline` · CLI `python3 -m nge.timeline --from 2026-07-01 --to 2026-07-14
[--pipe CGT] [--min-severity watch] [--as-known 2026-07-02T07:00]`.

### 5.6 Confidence scoring
Pass-through from events/impacts (no new derivation ⇒ no new confidence math —
deliberately). Entries render their band labels per §1.3.

### 5.7 Testing
Ordering + window filtering; status-at-as-of correctness (planned→active flip on
`valid_from`); `as_known` excludes later-recorded events (bitemporal golden: the
July-2 view must not show the July-3 posting); supersession display (both events,
correct statuses); requires the OI-1 corpus (≥8 events spanning statuses).

### 5.8 Risks & assumptions
Only as good as event derivation (§4 risks inherit). Timeline density at 5 pipes is
trivial; at 20+ pipes needs pagination — API already shaped for it (window + filters).

### 5.9 Extensibility
The `Timeline` payload is the future UI calendar/Gantt wire format. Recurring-
maintenance pattern detection ("Banner does this every June") is a read-only analytic
over the same table — listed for Era 3.

---

## 6. Capability: Morning Brief Generator

### 6.1 Business value
The first 30 minutes of a scheduler's day is triage: what posted overnight, what
changed, what threatens today's nominations. The brief compresses that to one ranked,
cited page — calibrated so that *everything above the fold is worth reading before the
Timely deadline*, and every line links back to its evidence.

### 6.2 Architecture (DDL-018)
Three stages: **assemble** (deterministic: events new/changed since last `brief_run` +
active events intersecting the as-of gas day, joined to their impacts) → **rank**
(explainable score: `severity_weight × exposure_factor × novelty × confidence`, where
exposure_factor scales with BP MDQ at affected points; every component printed in
`--explain` mode) → **render** (markdown template; sections: *Critical / Action /
Watch / FYI / Data-quality notes* — the last surfaces things like the SESH-4208
staleness). Optional fourth stage: **LLM polish** of section prose behind the §1.2
citation gate; on any gate failure the template text ships instead.

### 6.3 Data structures
`BriefItem{event, score, score_components, impacts, investigations, citations}`,
`Brief{as_of, generated_at, sections: dict[Severity, list[BriefItem]], data_quality_notes,
mode: template|polished}`.

### 6.4 Tables / entities
`brief_run` (§5.4). Nothing else — the brief is a pure read+render.

### 6.5 APIs
`generate(con, as_of, since?, llm?) -> Brief` · `render_markdown(Brief) -> str` ·
CLI `python3 -m nge.brief --as-of 2026-07-05 [--explain] [--no-llm]` → stdout/file.

### 6.6 Confidence scoring
Items carry event/impact confidence through; the ranking multiplies by confidence so a
0.6-confidence lead never outranks a 0.9 solid item of equal severity — but low-
confidence *critical* items are floored into the brief with an explicit
"unconfirmed — verify first" tag (missing a real FM because extraction confidence was
low is the worse failure mode).

### 6.7 Testing
Golden brief: fixture corpus at a fixed `as_of` ⇒ **byte-stable** markdown in template
mode (the regression test of record for the whole OI stack); ranking determinism +
component-sum property; novelty (second run same day ⇒ items demoted to unchanged);
citation-gate test plants an uncited sentence in a mock LLM response and asserts it is
stripped and the failure counted; `--no-llm` output contains zero LLM artifacts.

### 6.8 Risks & assumptions
Ranking weights are hypotheses until the user lives with them — kept in one visible
constants block, `--explain` makes disagreement diagnosable, and weight changes are
golden-file diffs (reviewable). Brief quality is corpus-bound (garbage-in honesty:
with 1 notice it's a demo; with OI-1's ~10 it's a real artifact).

### 6.9 Extensibility
Delivery = the rendered markdown handed to any channel (email/Slack/UI) — out of
scope here, interface ready. A scheduled trigger (CCR routine) turning it into a true
07:00 push is Era 3 and requires zero brief-code change.

---

## 7. Capability: Natural Language Query Layer

### 7.1 Business value
"How does this CGT maintenance affect SESH?" asked in English, answered from the
knowledge graph with citations — the original project vision. For a junior scheduler
it is also a tutor: every answer shows *how* it was derived (which mapping, which
edges, what confidence), building the mental model while answering the question.

### 7.2 Architecture (DDL-019)
**Intent registry, not text-to-SQL.** A whitelisted, typed registry of ~8 query
intents, each backed by a deterministic executor (mostly thin wrappers over §2–§6
APIs). Two routers map question → (intent, params): an LLM router (tool-use schema —
the model *must* pick a registered intent or `out_of_scope`) and a no-key
keyword/pattern router over the same registry. Out-of-scope questions get an explicit
refusal listing what *can* be asked — never a guess. Free-form SQL generation is
rejected by design: unauditable, injection-prone, untestable.

### 7.3 Data structures
```python
@dataclass Intent: name; params_schema; executor; examples: list[str]; description
REGISTRY = [asset_impact, events_in_window, path_between, point_lookup,
            contract_exposure, interconnect_partners, capacity_at_point, storage_status]
@dataclass NLAnswer: intent; params; result;  rendered: str; citations; router: llm|fallback
```
`capacity_at_point` and `storage_status` ship as *honest stubs* in OI-6: registered,
executable, returning "no OAC/storage-balance data loaded — here is what I *can* tell
you + what to check on the EBB" (the registry is complete even where data isn't).

### 7.4 Tables / entities
None. The registry is code (typed, versioned, tested), not data.

### 7.5 APIs
`ask(con, question, as_of, router=auto) -> NLAnswer` · CLI
`python3 -m nge.ask "what does the AlexSEG maintenance touch?"`. `nge/llm.py` appears
here: provider-agnostic client (DDL-004), `ANTHROPIC_API_KEY` from env, model id
configurable, single `complete(system, messages, tools?) -> Response` surface, no SDK
types leaking past the module boundary.

### 7.6 Confidence scoring
Two channels, kept separate and both shown: **routing confidence** (did we understand
the question — LLM router self-reports; fallback router scores pattern strength; below
threshold ⇒ ask-for-rephrase with intent suggestions rather than answer) and **answer
confidence** (pass-through from the executor per §1.3). They are never blended.

### 7.7 Testing
Router gold set (~20 question → expected intent+params pairs, phrasing variants
included) run against the **fallback router in CI always**, against the LLM router
when a key is present; executor correctness inherits §2–§6 tests; refusal golden
("what will Henry Hub basis do tomorrow?" ⇒ out_of_scope with capability list);
param-validation fuzz (malformed params never reach SQL).

### 7.8 Risks & assumptions
Fallback router is deliberately narrow — acceptable because the registry's `examples`
double as user-facing documentation of supported phrasings. LLM routing cost/latency
is per-question and small (single tool-use call); no conversation memory in OI-6
(each question independent — multi-turn is Era 3).

### 7.9 Extensibility
New intents = new registry entries with executors + gold questions (no router
changes). The registry schema *is* the future UI's query API and the future MCP-tool
surface, making the eventual "copilot" a rendering of already-tested capability.

---

## 8. Cross-capability testing strategy

1. **The golden AlexSEG scenario is the integration test of record**, extended at each
   milestone: notice → facts (Era 1, passing) → event(OI-1) → graph paths (OI-2) →
   propagation w/ severity *action* + ≥3 cited investigations (OI-3) → timeline entry
   with correct status transitions (OI-4) → above-the-fold brief item (OI-5) → correct
   NL answers for 3 phrasings (OI-6).
2. **Property/invariant suite** (new `tests/test_oi_invariants.py`): confidence
   non-increasing; no un-cited output element anywhere; hop bounds respected; template
   brief byte-stable; derivation idempotent.
3. **No-LLM mode is the default test mode** — CI never requires a key; LLM-path tests
   skip cleanly without one (same pattern as `HAS_DUCKDB` skips).
4. Every "the system can fail things" claim gets a planted-violation test (citation
   gate, router refusal, verifier), continuing the Era-1 discipline.

## 9. Consolidated risks & assumptions register

| # | Risk / assumption | Mitigation |
|---|---|---|
| R1 | Segment-map coverage (4 CGT segments) bounds impact precision | visible degradation to pipeline level; coverage tracked as an open DDL item; grows with notices, not code |
| R2 | Notice corpus size (1 → ~10 in OI-1) biases event/status-machine design to CGT idioms | derivation rules kept as data-like tables; per-pipe idioms added as observed |
| R3 | Public-data ceiling: no scheduled quantities ⇒ cannot predict flows | hard framing: exposure + investigations only; enforced in type names and docs |
| R4 | No API key in this environment today | every capability fully functional without; LLM paths additive |
| R5 | Ranking/severity weights are unvalidated hypotheses | single constants block; `--explain`; golden-diff review on change |
| R6 | DuckDB single-writer | fine for single-user tool; facade (DDL-020) isolates a future Postgres swap |
| R7 | Curated seeds (hubs, segment map) can encode errors | confidence+evidence per row; seed rows pinned by tests; user-verifiable at the desk |

## 10. Extensibility summary (Era 3 horizon)

More pipelines = rows, not code (adapters exist). OAC/storage-balance ingestion slots
into existing stub tables and upgrades propagation + two NL intents from qualitative
to quantitative. HTTP/UI wraps `nge/api.py` unchanged; `Graph.to_json()` and
`Timeline`/`Brief` payloads are the wire formats. Scheduled brief delivery is a
trigger + existing CLI. Multi-turn copilot = conversation state over the same intent
registry. Alerting = propagation engine + severity threshold + a notifier.
