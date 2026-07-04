# Evaluation Approach — how we know the engine is right

A copilot for a 5-month scheduler must be *measurably* correct, because the user cannot
yet catch a confident wrong answer (risk #10). Every layer gets an eval; nothing ships on
vibes.

## 1. Entity resolution (built)
- **Round-trip test** — `tests/test_interconnect_resolution.py` asserts SESH `83004` ↔
  CGT `4208` resolves both ways at confidence 1.0, that NA counterparties are never false
  positives, and that declared-external pipes are surfaced (not dropped).
- **Metric to track as we ingest more pipes:** resolution-tier distribution. Success =
  shrinking `declared_external` / `resolved_cid_only` as counterparty catalogs land.

## 2. Notice extraction (labeled, not yet automated)
- **Gold set**: hand-labeled facts per notice (first one:
  `data/fixtures/cgt_notice_26092015.expected_facts.json`). Grow to ~20 notices spanning
  Maintenance / Capacity Constraint / Force Majeure / revisions.
- **Metrics**: precision & recall on tuples (asset, metric, value_low, value_high,
  direction, cycle); plus a hard **span-fidelity** check (every predicted `source_span`
  must be a verbatim substring). Any hallucinated span = automatic fail for that fact.
- **Confidence calibration**: bucket by predicted confidence; measure actual accuracy per bucket.

## 3. Bitemporal correctness
- **Scenario test**: assert "AlexSEG estimated setting *as-of gas day 2026-07-08*
  *as-known 2026-07-01*" returns the Initiate value; after loading a hypothetical revision,
  the same as-of/as-known query is unchanged while the latest-known query reflects the revision.

## 4. Impact analysis (the v1 slice)
- **Traceable answer**: for "how does AlexSEG maintenance affect SESH?", the engine must
  return a *path* (CGT asset → CGT points → interconnect → SESH points → BP holdings) where
  every hop cites a row. No un-cited hop is allowed.
- **Human spot-check** against what actually happened (the notice's later Complete/curtailment
  postings) once we have a few real episodes.

## Non-goals for eval now
No UI, no latency/throughput SLAs, no automated extraction pipeline — those come after the
model and the answers are proven correct on the vertical slice.
