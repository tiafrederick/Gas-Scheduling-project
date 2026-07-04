"""Extraction eval harness tests (docs/eval-approach.md §2).

Stdlib-only. Two things are under test here:
  1. the BASELINE extractor reaches the accuracy floor on the gold fixture, and
  2. the SCORER itself — it must penalize wrong values and fabricated spans,
     otherwise the harness is a rubber stamp, not an anti-hallucination gate.
"""
import dataclasses
import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nge.extract import baseline_extract  # noqa: E402
from nge.extract.eval import FIXTURES_GLOB, load_gold, score_notice  # noqa: E402

GOLD_PATHS = sorted(glob.glob(FIXTURES_GLOB))
ALEXSEG = next(p for p in GOLD_PATHS if "26092015" in p)


class TestBaselineExtractor(unittest.TestCase):
    def test_perfect_score_on_alexseg_gold(self):
        s = score_notice(ALEXSEG, baseline_extract)
        self.assertEqual(s.n_gold, 3)
        self.assertEqual(s.n_pred, 3)
        self.assertEqual(s.n_correct, 3, msg=f"{s.mismatches} {s.span_violations}")
        self.assertEqual(s.precision, 1.0)
        self.assertEqual(s.recall, 1.0)

    def test_all_spans_are_verbatim(self):
        notice, _gold, body = load_gold(ALEXSEG)
        for f in baseline_extract(notice):
            self.assertIn(f.source_span, body)

    def test_key_values_extracted(self):
        notice, _gold, _body = load_gold(ALEXSEG)
        by_metric = {f.metric: f for f in baseline_extract(notice)}
        est = by_metric["estimated_capacity_setting"]
        self.assertEqual((est.value_low, est.value_high), (2325000.0, 2450000.0))
        self.assertEqual(est.direction, "backhaul")
        self.assertEqual(est.effective_cycle, "TIMELY")
        self.assertEqual(est.valid_gas_day_from, "2026-07-08")
        self.assertEqual(est.valid_gas_day_to, "2026-07-10")
        self.assertEqual(by_metric["design_capacity"].value_low, 2683256.0)


class TestScorerCatchesBadExtractions(unittest.TestCase):
    """The harness must FAIL things: a scorer that can't reject is worthless."""

    def test_wrong_value_lowers_precision_and_recall(self):
        def corrupted(notice):
            facts = baseline_extract(notice)
            return [dataclasses.replace(f, value_low=9999999.0)
                    if f.metric == "estimated_capacity_setting" else f
                    for f in facts]
        s = score_notice(ALEXSEG, corrupted)
        self.assertEqual(s.n_correct, 2)
        self.assertLess(s.precision, 1.0)
        self.assertLess(s.recall, 1.0)
        self.assertTrue(any("value_low" in m for m in s.mismatches))

    def test_fabricated_span_is_zeroed_even_with_correct_values(self):
        """Hallucination gate: right answer + invented citation = REJECTED."""
        def hallucinated(notice):
            return [dataclasses.replace(f, source_span="this text is not in the notice")
                    for f in baseline_extract(notice)]
        s = score_notice(ALEXSEG, hallucinated)
        self.assertEqual(s.n_correct, 0)
        self.assertEqual(len(s.span_violations), 3)
        self.assertEqual(s.recall, 0.0)

    def test_missed_facts_lower_recall(self):
        def lazy(notice):
            return baseline_extract(notice)[:1]
        s = score_notice(ALEXSEG, lazy)
        self.assertEqual(s.n_pred, 1)
        self.assertEqual(s.recall, 1 / 3)
        self.assertEqual(len(s.missed), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
