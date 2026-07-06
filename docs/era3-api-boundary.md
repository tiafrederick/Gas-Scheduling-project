# Era 3 — Architecture Proposal: A Stable Application Boundary Over the Engine

> **Status: design, not implementation.** This is the architectural contract for
> exposing the Era-2 reasoning engine (`nge/api.py::Engine`) to external clients.
> Design authority for Era 2 is [`operational-intelligence.md`](./operational-intelligence.md);
> decision record is [`design-decision-log.md`](./design-decision-log.md) (DDL-020 is
> the facade decision this document extends). The concrete build order derived from
> this proposal lives in [`roadmap.md`](./roadmap.md) (Era 3 section).

---

## 0. Framing: the first "interface" is not a transport

"HTTP or MCP or gRPC?" is the wrong first question — those are *transports*. The
durable boundary is a **transport-agnostic Operation Contract**: a versioned,
self-describing set of named operations with typed request/response schemas and stable
serialization. Build it once; every transport becomes a thin adapter that only decodes
a request into an operation call and encodes the structured result. **No business logic
in any adapter.** The Engine stays the single source of reasoning.

```
Engine (reasoning core)
   │  in-process, typed
   ▼
Operation Contract   ← the boundary
   │  structured JSON, versioned, self-describing
   ├── MCP adapter        (transport #1 — recommended first)
   ├── HTTP/JSON adapter   (transport #2 — when a browser/remote client exists)
   └── desktop / in-proc   (transport #3 — trivial; it already is one)
```

---

## 1. Recommended interface — and why not HTTP first

**Define the Operation Contract now; expose it first through MCP, not HTTP.**

The near-term consumer is not a web app — it is the scheduler's copilot answering *"how
does this CGT maintenance affect SESH?"* (the original vision). MCP is built for exactly
that: named tools, JSON-Schema inputs, structured results, self-description. The Engine
already produces both things MCP needs — a **capability/intent registry with param
schemas** (DDL-019 called the registry "the future UI/MCP-tool surface") and **typed,
cited results.** The impedance mismatch is near zero.

HTTP is transport #2, not #1:

1. **The domain is RPC, not REST.** Operations are *computations* ("compute an impact
   assessment as-of a gas day"), not *resources* with CRUD lifecycles. `GET
   /impact/AlexSEG` invents a resource model the domain doesn't have.
2. **Premature infrastructure.** HTTP-first means auth, TLS, CORS, rate limiting,
   OpenAPI, deployment — for a single-user local desk tool. DDL-020 already rejected
   premature HTTP.
3. **MCP forces the contract to be honest first.** Self-describing schemas + structured
   results make you nail serialization, the error model, and the tool boundary before a
   frontend exists. HTTP then falls out as a mechanical projection.
4. **Least surface, most value.** The copilot delivers the vision to the desk with no
   bespoke frontend.

### Tradeoffs

| | Upside | Cost / risk | Mitigation |
|---|---|---|---|
| Contract-first (the boundary) | One contract, N transports; each thin + testable | One extra indirection; discipline to not let MCP shape the contract | Contract defined against the domain; MCP adapter generated from it |
| MCP transport #1 | Matches the copilot consumer; self-describing; no web infra | MCP spec evolving; not browser-consumable; harder to `curl` | Keep CLIs as a debug transport; add HTTP when a browser client appears |
| HTTP deferred | Avoids auth/serving/deploy for a local tool | Remote/browser clients wait for transport #2 | Transport #2 is a thin adapter — cheap because the contract exists |

**Hard rule:** no reasoning, formatting, or SQL in a transport adapter. The moment an
adapter contains an `if` about business meaning, the boundary has failed.

---

## 2. Operation design

Operations, not endpoints. Names are the stable contract; each maps 1:1 to an Engine
method, an MCP tool, and later `POST /v1/operations/{name}`.

| Operation | Kind | Temporal | Honors `as_known` | Engine method |
|---|---|---|---|---|
| `impact` | query | yes | not yet → typed error | `Engine.impact` |
| `timeline` | query | yes | **yes** | `Engine.timeline` |
| `brief` | query | yes | not yet → typed error | `Engine.brief` (read-only) |
| `brief.record` | **command** | yes | no | `Engine.brief(record=True)` |
| `ask` | query | yes | no | `Engine.ask` |
| `graph.path` | query | **atemporal** | n/a | `Engine.path` |
| `graph.neighbors` | query | atemporal | n/a | `Engine.neighbors` |
| `graph.stats` | query | atemporal | n/a | `Engine.graph_stats` |
| `describe` | meta | n/a | n/a | **new** — operation registry + schemas |

- **Queries and commands are separated.** Everything is a pure read except one mutation
  (`brief` recording a `brief_run` for novelty). Splitting `brief.record` out makes the
  read path safely cacheable and the single write explicit.
- **`describe` is first-class.** Clients discover capabilities, param schemas, enum
  vocabularies, and the contract version at runtime instead of hardcoding them. It is
  what MCP `tools/list` and an OpenAPI doc both generate *from*.
- **Atemporal operations declare themselves** (`as_of: null`, reject `as_known`).

---

## 3. Request / response schema

### 3.1 Uniform envelope (stable within a major version)

```jsonc
// Request
{ "contract_version": 1, "operation": "impact", "params": { "asset": "AlexSEG" },
  "as_of": "2026-07-08", "as_known": null }

// Response
{ "contract_version": 1, "operation": "impact",
  "query":   { "as_of": "2026-07-08", "as_known": null },
  "dataset": { "snapshot_id": "sha-…", "built_at": "…", "boundary": "public_ferc_postings" },
  "result":  { /* capability payload — §3.3 */ },
  "warnings":[ { "code": "unverified_facts", "message": "…" } ],
  "error":   null }
```

The **envelope is the stable surface**; the **payload is where capabilities grow.**
`dataset.snapshot_id` is the highest-leverage addition (see §9, risk #1).

### 3.2 Shared value objects

Every capability emits the same *shape* for the same *idea*; define once, compose
everywhere:

```jsonc
"Citation":   { "kind": "evt|fact|point|ic|segmap|holding|pipeline|hub",
                "ref": "evt:87259e6a873181bd" }          // OPAQUE — do not parse
"Confidence": { "value": 0.90, "band": "solid|probable|lead", "hops": 2 }
"Claim":      { "statement": "…", "confidence": Confidence,
                "citations": [Citation], "verified": true }
"Fact":       { "metric": "estimated_capacity_setting", "low": 2325000, "high": 2450000,
                "uom": "Dth/d", "confidence": Confidence, "verified": false,
                "source_span": "…", "citation": Citation }
```

Because *"a cited, confidence-graded claim"* has one shape everywhere, a new capability
does not invent a new provenance format.

### 3.3 Capability payloads (sketch — additive)

**`impact`:**
```jsonc
{ "subject": { "asset": "AlexSEG", "event": { "ref": "evt:…", "name": "…", "type": "maintenance" } },
  "window": { "from": "2026-07-08", "to": "2026-07-10", "status_as_of": "active" },
  "severity": { "level": "action", "components": [ "13.4% cut ⇒ watch", "Primary Firm ⇒ action" ] },
  "facts": [ Fact, … ],
  "segment": { "seg_cd": "ALEXDRIA", "confidence": Confidence, "evidence": "…" },
  "exposures": [ { "reason": "contract_at_affected_point", "subject": "holding:…",
                   "hop": 0, "severity": "action", "confidence": Confidence,
                   "investigation": "…", "citations": [Citation] }, … ],
  "confidence": Confidence, "citations": [Citation] }
```

**`timeline`** → `{ "window": {...}, "entries": [ TimelineEntry ] }` (flat entries: event
ref, pipe, asset, type, seg, lifecycle, valid_from/to, window_source, confidence,
severity, status_as_of, n_sources, superseded_by).

**`brief`** → `{ "sections": { "critical":[BriefItem], "action":[…], "watch":[…],
"informational":[…] }, "data_quality": [Claim], "mode": "template|polished", "summary":
null }`; each `BriefItem` carries `score` + `score_components`, `exposure_summary`,
`investigations`, `citations`, `unconfirmed`.

**`ask`** (two confidence channels stay separate):
```jsonc
{ "routing": { "router": "fallback|llm", "intent": "asset_impact", "params": {...},
               "confidence": 0.85, "out_of_scope": false },
  "answer":  { /* ideally the SAME shape as the operation it resolved to */ },
  "answer_confidence": Confidence, "citations": [Citation] }
```

**`graph.path`** → `{ "origin","destination","paths":[ { "hops",int, "confidence":Confidence,
"edges":[ { "from","to","flow":"receives_from|delivers_to|bidirectional",
"confidence":Confidence,"citation":Citation } ] } ] }`.

---

## 4. Serialization strategy

- **`to_dict()` on every `*Response` and wrapped payload object**, producing the schemas
  above. This is the core Engine change (§6). The transport just JSON-encodes.
- **`render()` stays** for CLIs/humans — it is *not* the wire format. Text and JSON are
  two projections of the same objects; never derive one from the other.
- **Serialization is centralized** on the shared value objects — one place decides how a
  `Citation`/`Confidence`/`Claim` looks on the wire.
- **ISO-8601 dates, opaque string ids, explicit `uom` on every quantity.** No bare
  numbers on the wire — the Era-1 discipline extends outward.
- **Enums are closed, published vocabularies** (`severity`, `lifecycle_status`,
  `reason_code`, `resolution_status`, `flow`); `describe` is their source of truth.

---

## 5. Versioning strategy

- **One integer `contract_version` in the envelope**, starting at `1`; changes only on a
  *breaking* change (removed/retyped field, narrowed enum, changed semantic).
- **Additive-only within a major version.** New operations, payload fields, appended enum
  values, warning codes — all non-breaking. Clients MUST ignore unknown fields.
- **`describe` may carry a per-operation `schema_hash`** so additive changes are
  detectable and a CI pipeline can diff contracts. *(See §11 — this is deferred as
  premature until an external contract consumer exists.)*
- **Deprecation is a `warnings` entry, not a silent removal.**
- **Transports version independently of the contract** (`/v1` on HTTP vs
  `contract_version` on the payload — different clocks).

---

## 6. Changes to the Engine before exposing it publicly

Ordered by leverage:

1. **Structured serialization (`to_dict()`).** Non-negotiable prerequisite. Today only
   `render()` exists; some payloads (`NLAnswer.render`, `PathResult.explain`) compute
   prose on the fly and never expose fields structurally.
2. **`ask` executors return structured data, not a prose blob.** `ExecResult.text` is a
   rendered string; an `ask` answer should carry the *same payload* as the operation it
   resolved to. Deepest change, most likely to be skipped — flag it now.
3. **Error-as-data with a typed taxonomy.** `AsKnownUnsupported` is a Python exception;
   `error` is prose. Need a closed error-code set surfaced as data (§7).
4. **A machine-readable operation registry** as single source of truth: `{ name,
   description, params_schema, response_ref, kind, temporal, honors_as_known }`.
   Generates MCP tools, OpenAPI, and `describe`.
5. **A concurrency/session model.** `Engine` holds one read-only connection and assumes
   a single caller. A server needs connection-per-request / per-worker / small pool
   (read-only is safe to share; DuckDB thread-safety must be pinned).
6. **Snapshot/provenance identity** (`dataset.snapshot_id`, `built_at`). Without it,
   citations and `as_known` are un-auditable and un-cacheable across store rebuilds (§9,
   risk #1).
7. **Boundary input validation.** Params now arrive from untrusted transports; validate
   against the registry schema and return `invalid_params` before an executor runs.
8. **Neutralize `nge.reach`.** Must not appear in the registry or any transport.

None of these change *reasoning* — they harden the *boundary*.

---

## 7. Error model

Errors are **data in the envelope**, never transport-level surprises:

```jsonc
"error": { "code": "as_known_unsupported",
           "message": "impact does not reconstruct historical knowledge; use timeline.",
           "operation": "impact", "retriable": false,
           "details": { "supported_by": ["timeline"] } }
```

- **Two error planes, kept distinct.** *Contract errors* (bad operation/params,
  unsupported feature) are envelope errors with a code. *Graceful domain outcomes*
  (unknown asset, no path, out-of-scope question) are **not errors** — they are valid,
  cacheable results with low/zero confidence and a message (as the Engine already does).
  "I understood you and nothing matches" is a success, not a failure.
- **Transport status codes are a projection** (`invalid_params → 400`,
  `as_known_unsupported → 422`, `not_implemented → 501`; MCP returns the error object).
  `error.code` is authoritative across all transports.

---

## 8. Backward compatibility

- **Opaque tokens are the load-bearing guarantee.** Citations/refs/snapshot ids are
  strings clients must not parse — internal id schemes can change *provided* stability is
  scoped to a snapshot (§9).
- **Additive-only + ignore-unknown-fields**, stated contractually.
- **Enum evolution is a versioned event** — appending a `severity` band is additive but
  semantically breaking for a client that switches on the set, so it ships a `warnings`
  note and a `describe` diff. Narrowing/renaming is a major-version change.
- **The `ask` LLM router must not leak nondeterminism into the contract.** The
  deterministic executors are the contract; the LLM only *chooses which* runs. The
  response makes this explicit (`routing.router`, `routing.confidence`), and a question
  can be pinned to the `fallback` router for reproducibility.
- **CLIs remain a supported transport** during migration.

---

## 9. Architectural risks

1. **Citation durability (highest).** `event_uid`s are content hashes; if derivation
   changes they change, so a citation stored yesterday may not resolve against a rebuilt
   store. **Mitigation:** scope citation validity to `dataset.snapshot_id`; publish the
   guarantee as *"resolvable within its snapshot; across snapshots, re-query."*
2. **Enum/vocabulary drift** between core and published contract. **Mitigation:** freeze
   public vocabularies; map internal proliferation to the stable set.
3. **Concurrency correctness** (DuckDB under a server). **Mitigation:** pin the model
   (read-only pool; serialize the single writer, `brief.record`).
4. **`ask` structure debt.** Prose in a `string` field looks structured but isn't.
   **Mitigation:** land change #2 in §6 before publishing `ask`.
5. **Adapter logic creep.** **Mitigation:** a conformance suite runs the same operation
   in-process and through each transport and asserts identical results.
6. **MCP spec churn.** **Mitigation:** thin adapter generated from the registry.

---

## 10. Rejected alternatives

- **HTTP/REST first** — resource/CRUD semantics don't fit a computation engine;
  premature infra; verb/URL bikeshedding. Fine as transport #2.
- **GraphQL** — arbitrary client field-selection over a reasoning engine couples clients
  to internal shape and invites expensive/ambiguous queries; confidence/provenance and
  coarse "compute an assessment" operations don't map to a field graph.
- **Auto-generated RPC off Engine signatures** — leaks Python types/names, no stable
  schema, no versioning, nowhere for the envelope. A re-export, not a contract.
- **gRPC/protobuf** — great typed polyglot RPC, heavyweight for a local single-user tool
  and mismatched to the copilot-first consumer. Revisit for polyglot services.
- **`render()` text over the wire** — destroys structure, makes citations unparseable.

---

## 11. Explicitly deferred as premature (self-challenge)

Recorded here so the roadmap doesn't over-build:

- **`schema_hash` + CI contract-diffing** — no external contract consumer yet.
- **Citation durability *across rebuilds*** — keep the `snapshot_id` field now; defer the
  hard cross-rebuild guarantee until a client caches.
- **`retriable` + `details` on errors** — `{code, message, operation}` suffices at first.
- **Deprecation-warning lifecycle policy** — nothing external to deprecate yet.
- **Connection pool** — deferrable while the first transport is single-session (MCP
  stdio); required at HTTP/UI.

---

## 12. If I were joining today as Principal Architect

**Make the contract's *stability guarantees* explicit and machine-checkable before any
external client depends on them** — the Engine is correct, but its identifiers and
vocabularies were built for internal determinism, not outside consumers who will cache,
cite, and audit. In priority order: (1) scope citation/id stability to a `snapshot_id`
and publish the guarantee; (2) add structured serialization and make `ask` structured;
(3) freeze the public enum vocabularies; (4) turn exceptions into a closed serialized
error taxonomy and isolate the one write; (5) decide the concurrency model; (6) *then*
add transports — MCP first, HTTP second, each thin and conformance-tested. Do **not**
touch the reasoning core, the deterministic-core/LLM-shell split, the min-composition
confidence algebra, the citation gate, or the bitemporal model — Era 2 got the hard part
right.
