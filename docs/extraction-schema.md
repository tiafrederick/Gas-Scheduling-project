# Notice Extraction — schema + worked example (AlexSEG)

The dangerous step (risk #2): capacity numbers live in **prose**, and a hallucinated or
mis-parsed Dth value is worse than no value for a junior scheduler who can't yet catch it.
So extraction targets a **typed schema with mandatory provenance**, and every fact is
gated by `confidence` + `verified_by`.

Target model: [`CapacityImpactFact`](../src/nge/models/facts.py). Gold label:
[`data/fixtures/cgt_notice_26092015.expected_facts.json`](../data/fixtures/cgt_notice_26092015.expected_facts.json).
Source notice: [`data/fixtures/cgt_notice_26092015.txt`](../data/fixtures/cgt_notice_26092015.txt).

## Principles
1. **Structured header via deterministic parse; body via LLM-assisted extraction.** Never
   ask the model to invent header fields it can read directly.
2. **Every value keeps its `source_span`** — the exact substring it came from. No span → reject.
3. **Controlled vocabularies** (`metric`, `direction`, `effective_cycle`, `affects_services`)
   — the model must map into them, and construction raises on anything off-list.
4. **`confidence` + `verified_by`.** Reasoning may weight/trust facts by confidence; a human
   sign-off (`verified_by`) is the gate before any high-stakes use.
5. **Extraction ≠ reasoning** (DDL-004). Extraction may use a cheap/fast model; reasoning
   uses Fable 5. They are separate calls with separate prompts and separate evals.

## Worked example — CGT notice 26092015 (AlexSEG, Jul 8–10 2026)
From: *"the backhaul capacity through AlexSEG will be reduced to a level between
2,325,000 - 2,450,000 Dth effective Timely Cycle for Gas Day Wednesday, July 8, 2026."*
plus the `AlexSEG` bullet block, we extract **3 facts** (all provenance-tagged):

| asset | metric | low | high | uom | dir | cycle | gas-day window |
|---|---|---|---|---|---|---|---|
| AlexSEG | estimated_capacity_setting | 2,325,000 | 2,450,000 | Dth | backhaul | TIMELY | 2026-07-08 → 07-10 |
| AlexSEG | design_capacity | 2,683,256 | 2,683,256 | Dth | backhaul | — | — |
| AlexSEG | posted_percentage | 87 | 91 | percent | backhaul | — | — |

Plus header facts (deterministic): type=Maintenance, stat=Initiate, critical=Y,
affects services = {Primary Firm, Secondary Firm, Interruptible}, RCC 7-day average
usage window = Jun 13–19 2026 (tariff GT&C Section 40).

Validated end-to-end: `nge.models.CapacityImpactFact(**fact)` constructs cleanly for all
three and enforces vocab + non-empty `source_span`.

## Extraction contract (for the future LLM step — not built yet)
- Input: notice header (parsed) + body text.
- Output: `list[CapacityImpactFact]`, each with `source_span`, `extraction_method="llm"`,
  `extraction_model`, `confidence`.
- Reject any fact whose `source_span` is not a verbatim substring of the body (anti-hallucination guard).
- Unknown asset→point mapping is allowed (`related_point_uids=[]`); resolution happens later.

## Eval hook
The gold JSON is the first labeled example for the extraction eval in
[`eval-approach.md`](./eval-approach.md): precision/recall on (asset, metric, value,
direction, cycle) with exact-span checking.
