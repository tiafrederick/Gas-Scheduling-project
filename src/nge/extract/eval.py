"""Extraction eval harness (docs/eval-approach.md §2).

Grades any extractor against the hand-labeled gold fixtures:
  match     predicted <-> gold facts on (asset_name, metric)
  correct   all of (value_low, value_high, uom, direction, effective_cycle,
            valid_gas_day_from, valid_gas_day_to) equal
  SPAN GATE predicted source_span must be a VERBATIM substring of the notice
            body — any violation zeroes that prediction regardless of values.
            This is the anti-hallucination rule: an extractor may be wrong,
            but it may not cite text that does not exist.

Run:  PYTHONPATH=src python3 -m nge.extract.eval [--extractor baseline]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass, field

from ..models.facts import CapacityImpactFact, Notice

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FIXTURES_GLOB = os.path.join(REPO, "data", "fixtures", "*.expected_facts.json")

TUPLE_FIELDS = ("value_low", "value_high", "uom", "direction", "effective_cycle",
                "valid_gas_day_from", "valid_gas_day_to")


def _key(f) -> tuple[str, str]:
    return ((f.asset_name or "").lower(), f.metric)


def _get(f, name):
    return getattr(f, name)


@dataclass
class NoticeScore:
    notice_uid: str
    n_gold: int = 0
    n_pred: int = 0
    n_correct: int = 0
    span_violations: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.n_correct / self.n_pred if self.n_pred else 0.0

    @property
    def recall(self) -> float:
        return self.n_correct / self.n_gold if self.n_gold else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def load_gold(path: str) -> tuple[Notice, list[CapacityImpactFact], str]:
    with open(path, encoding="utf-8") as fh:
        gold = json.load(fh)
    notice = Notice(**gold["notice"])
    with open(os.path.join(REPO, notice.source_file), encoding="utf-8") as fh:
        body = fh.read()
    notice.body_text = body
    facts = [CapacityImpactFact(**f) for f in gold["capacity_impact_facts"]]
    return notice, facts, body


def score_notice(gold_path: str, extractor) -> NoticeScore:
    notice, gold_facts, body = load_gold(gold_path)
    predicted = extractor(notice)

    s = NoticeScore(notice_uid=notice.notice_uid,
                    n_gold=len(gold_facts), n_pred=len(predicted))

    gold_by_key = {_key(f): f for f in gold_facts}
    matched_gold: set[tuple[str, str]] = set()

    for p in predicted:
        # HARD GATE: span must exist verbatim in the notice body.
        if p.source_span not in body:
            s.span_violations.append(
                f"{_key(p)}: span not in body: {p.source_span[:60]!r}")
            continue
        g = gold_by_key.get(_key(p))
        if g is None or _key(p) in matched_gold:
            s.mismatches.append(f"{_key(p)}: no unmatched gold fact with this key")
            continue
        diffs = [fld for fld in TUPLE_FIELDS if _get(p, fld) != _get(g, fld)]
        if diffs:
            s.mismatches.append(
                f"{_key(p)}: field mismatch on {diffs} "
                f"(pred {[_get(p, d) for d in diffs]} vs gold {[_get(g, d) for d in diffs]})")
            continue
        matched_gold.add(_key(p))
        s.n_correct += 1

    s.missed = [f"{k}" for k in gold_by_key if k not in matched_gold]
    return s


def run(extractor_name: str = "baseline") -> list[NoticeScore]:
    from . import EXTRACTORS
    extractor = EXTRACTORS[extractor_name]
    scores = [score_notice(p, extractor) for p in sorted(glob.glob(FIXTURES_GLOB))]
    return scores


def main() -> None:
    ap = argparse.ArgumentParser(description="Grade an extractor against gold fixtures")
    ap.add_argument("--extractor", default="baseline")
    args = ap.parse_args()

    scores = run(args.extractor)
    if not scores:
        print("No gold fixtures found:", FIXTURES_GLOB)
        return

    print(f"EXTRACTION EVAL — extractor '{args.extractor}'")
    print("=" * 70)
    tot_gold = tot_pred = tot_ok = 0
    for s in scores:
        tot_gold += s.n_gold; tot_pred += s.n_pred; tot_ok += s.n_correct
        print(f"{s.notice_uid}: gold={s.n_gold} pred={s.n_pred} correct={s.n_correct}"
              f"  P={s.precision:.2f} R={s.recall:.2f} F1={s.f1:.2f}")
        for v in s.span_violations:
            print(f"   SPAN VIOLATION  {v}")
        for m in s.mismatches:
            print(f"   mismatch        {m}")
        for m in s.missed:
            print(f"   missed gold     {m}")
    p = tot_ok / tot_pred if tot_pred else 0.0
    r = tot_ok / tot_gold if tot_gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    print("-" * 70)
    print(f"AGGREGATE over {len(scores)} notice(s): "
          f"P={p:.2f} R={r:.2f} F1={f1:.2f}  (span gate: verbatim-substring)")


if __name__ == "__main__":
    main()
