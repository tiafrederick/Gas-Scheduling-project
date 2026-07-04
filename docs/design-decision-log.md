# Design Decision Log (DDL)

Living record of architectural decisions. Status: Proposed → Accepted → (Superseded).
Challenge any of these; that's the point.

| # | Decision | Status | Rationale / notes |
|---|---|---|---|
| DDL-001 | Canonical system-of-record = **bitemporal relational** model; knowledge graph is a **derived** read-model | Accepted | Graphs are weak at versioned/temporal/auditable facts; relational core + graph projection gets both. |
| DDL-002 | Keys: companies by **FERC CID**; points by composite **`(TSP FERC CID, LOC)`**; interconnects resolved from `Up/Dn FERC CID` + `Up/Dn Loc` | Accepted | Proven in the interconnect spike (round-trip 83004↔4208). |
| DDL-003 | Notices = **immutable events** w/ lifecycle (Initiate/Update/Complete) + supersession; header parsed deterministically, body extracted to **typed facts** w/ provenance + confidence + source span | Accepted | Anti-hallucination + bitemporal auditability. |
| DDL-004 | LLM behind a **provider-agnostic** interface; **extraction ≠ reasoning**; no coupling to Fable 5 | Accepted | Model choice will change; different jobs, different models. Fable 5 = reasoning + (later) UI iteration. |
| DDL-005 | **No UI** in Phase 0/1; thinking workspace = canonical store + notebooks/CLI | Accepted (user) | Avoid encoding an immature mental model into software. |
| DDL-006 | **Hybrid intake**: manual export drops now; adapter interfaces designed but automation OFF | Accepted (user) | ToS-safe + reliable for v1; automation later per-portal. |
| DDL-007 | **Personal/local, public data only**; cloud LLM permitted; positions/nominations excluded | Accepted (user) | Index-of-Customers holdings are public FERC postings. |
| DDL-008 | v1 = **cross-pipeline impact analysis** (AlexSEG → SESH deliveries) | Accepted (user) | Hardest, highest-value slice; proves the thesis. |
| DDL-009 | **Portfolio commercial reachability** (CGT+SESH+Sabine + direct interconnects, direction flag, no hydraulics) | Accepted (user) | Enough for impact/alternate-path; avoids over-modeling. |
| DDL-010 | **No graph database**; reachability via recursive SQL / in-memory `networkx` | Proposed | 3-pipe scale doesn't justify Neo4j ops burden. |
| DDL-011 | Stack: **Python + DuckDB** canonical store + typed models (dataclass→Pydantic) + provider-agnostic LLM client | Proposed | Confirm at Phase 1 kickoff. Spike/tests kept stdlib-only. |

## Open items
- Confirm Python + DuckDB stack (DDL-011) or alternative.
- Storage (Egan/Bobcat/Sabine Hub) balance data — availability + shape.
- First follow-on artifact: broaden ingestion (more pipe point catalogs) vs. build the
  DuckDB load vs. wire the LLM extraction step vs. a written "portfolio map / scheduling-101"
  learning doc.
- `operational_capacity_fact` columns — finalize against a real OAC/OA_MLC table.
