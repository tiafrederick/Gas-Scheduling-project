"""Morning Brief tests (OI-5; OI doc §6).

The golden brief is the regression test of record for the whole OI stack: a
fixed corpus at a fixed as_of must render byte-identical markdown in template
mode. Around it: ranking determinism + the component-product property, the
novelty demotion mechanic, the load-bearing Data-quality section (SESH->4208),
the no-LLM zero-artifact guarantee, and the citation-gated polish seam.
"""
import importlib.util
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "brief_2026-07-05.md")
AS_OF = date(2026, 7, 5)
GEN_AT = datetime(2026, 7, 5, 6, 30, 0)


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class BriefBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def gen(self, **kw):
        from nge.brief import generate
        kw.setdefault("as_of", AS_OF)
        kw.setdefault("since", None)
        kw.setdefault("generated_at", GEN_AT)
        return generate(self.con, **kw)


class TestGolden(BriefBase):
    def test_template_render_is_byte_stable(self):
        from nge.brief import render_markdown
        got = render_markdown(self.gen(), explain=False)
        with open(GOLDEN, encoding="utf-8") as fh:
            want = fh.read()
        self.assertEqual(got, want, "brief render drifted from the golden; if "
                         "intended, regenerate tests/golden/brief_2026-07-05.md")

    def test_regenerating_is_zero_diff(self):
        from nge.brief import render_markdown
        self.assertEqual(render_markdown(self.gen()), render_markdown(self.gen()))


class TestAssembly(BriefBase):
    def test_four_items_active_or_upcoming(self):
        names = [it.asset_name for it in self.gen().all_items()]
        self.assertEqual(len(names), 4)
        self.assertIn("Corinth Compressor Station", names)
        self.assertIn("Alexandria and Chicot Compressor Station", names)
        self.assertIn("Banner Compressor Station", names)
        # long-completed June jobs are NOT dumped into the first brief
        self.assertNotIn("Paradis Lateral Pipeline", names)
        self.assertNotIn("Grayson Compressor Station", names)

    def test_sections_ordered_worst_first(self):
        from nge.brief import render_markdown
        md = render_markdown(self.gen())
        self.assertLess(md.index("## Critical"), md.index("## Action"))
        self.assertLess(md.index("## Action"), md.index("## Watch"))
        self.assertLess(md.index("## Watch"), md.index("## FYI"))

    def test_superseded_events_excluded(self):
        names = [it.asset_name for it in self.gen().all_items()]
        self.assertNotIn("CAPACITY POSTING - TIM for June 23, 2026", names)


class TestRanking(BriefBase):
    def _item(self, brief, substr):
        return next(it for it in brief.all_items() if substr in it.asset_name)

    def test_component_product_property(self):
        for it in self.gen().all_items():
            c = it.components
            self.assertAlmostEqual(
                it.score,
                round(c.severity_weight * c.exposure_factor * c.novelty
                      * c.confidence, 3), places=6)

    def test_bp_exposure_lifts_alexseg(self):
        """AlexSEG carries the only BP contract exposure in the corpus -> its
        exposure_factor is >1 while the unexposed items stay at 1.0."""
        b = self.gen()
        alex = self._item(b, "Alexandria")
        banner = self._item(b, "Banner")
        self.assertEqual(alex.components.exposure_factor, 1.25)
        self.assertEqual(banner.components.exposure_factor, 1.0)

    def test_low_confidence_critical_tagged_unconfirmed(self):
        b = self.gen()
        corinth = self._item(b, "Corinth")
        self.assertEqual(corinth.severity, "critical")
        self.assertLess(corinth.components.confidence, 0.6)
        self.assertTrue(corinth.unconfirmed)

    def test_determinism_two_runs_identical(self):
        a = [(it.event_uid, it.score) for it in self.gen().all_items()]
        b = [(it.event_uid, it.score) for it in self.gen().all_items()]
        self.assertEqual(a, b)


class TestNovelty(BriefBase):
    def _item(self, brief, substr):
        return next(it for it in brief.all_items() if substr in it.asset_name)

    def test_second_run_demotes_seen_items(self):
        """A brief run after everything posted -> every item is 'already
        briefed' (novelty 0.5), halving its score but preserving order."""
        first = self.gen(since=None)
        later = self.gen(since=datetime(2026, 7, 6, 0, 0))
        a0 = self._item(first, "Alexandria")
        a1 = self._item(later, "Alexandria")
        self.assertEqual(a0.components.novelty, 1.0)
        self.assertEqual(a1.components.novelty, 0.5)
        self.assertAlmostEqual(a1.score, a0.score / 2, places=6)

    def test_partial_novelty_by_post_dt(self):
        """since between two postings: the July 3-6 posting (posted 07-02) is
        still new; AlexSEG (posted 07-01) is demoted."""
        b = self.gen(since=datetime(2026, 7, 1, 12, 0))
        newer = self._item(b, "July 3, 2026")
        older = self._item(b, "Alexandria")
        self.assertEqual(newer.components.novelty, 1.0)
        self.assertEqual(older.components.novelty, 0.5)


class TestDataQuality(BriefBase):
    def test_sesh_4208_staleness_surfaced(self):
        """DoD extra: the cross-EBB staleness must appear under Data-quality,
        cited to both interconnect rows and the retired point."""
        notes = self.gen().data_quality
        stale = next(n for n in notes if "4208" in n.text)
        self.assertIn("RETIRED", stale.text)
        self.assertIn("83004", stale.text)
        self.assertIn("83104", stale.text)         # both SESH points, one note
        self.assertIn("point:C000307:4208", stale.citations)
        self.assertIn("ic:35b371eb182cf768", stale.citations)

    def test_unmapped_fm_flagged(self):
        notes = self.gen().data_quality
        self.assertTrue(any("Corinth" in n.text and "no segment mapping" in n.text
                            for n in notes))

    def test_unverified_figures_flagged(self):
        notes = self.gen().data_quality
        self.assertTrue(any("Alexandria" in n.text and "extraction-only" in n.text
                            for n in notes))


class TestNoLlmAndPolish(BriefBase):
    def test_template_mode_has_zero_llm_artifacts(self):
        from nge.brief import render_markdown
        b = self.gen()                              # narrator=None
        self.assertEqual(b.mode, "template")
        self.assertIsNone(b.summary)
        self.assertNotIn("## Summary", render_markdown(b))

    def test_polish_adopts_clean_cited_prose(self):
        def narrator(bundle, valid):
            uid = sorted(valid)[0]
            return f"The desk should prioritize today's flagged items [{uid}]."
        b = self.gen(narrator=narrator)
        self.assertEqual(b.mode, "polished")
        self.assertIsNotNone(b.summary)

    def test_polish_falls_back_on_unknown_uid(self):
        def narrator(bundle, valid):
            return "Everything looks calm today [evt:fabricated]."
        b = self.gen(narrator=narrator)
        self.assertEqual(b.mode, "template")        # gate failed -> template stands
        self.assertIsNone(b.summary)

    def test_polish_strips_uncited_but_keeps_cited(self):
        def narrator(bundle, valid):
            uid = sorted(valid)[0]
            return (f"Focus on the flagged items [{uid}]. "
                    f"Unrelated market chatter here.")   # uncited -> stripped
        b = self.gen(narrator=narrator)
        self.assertEqual(b.mode, "polished")
        self.assertIn("Focus on the flagged items", b.summary)
        self.assertNotIn("market chatter", b.summary)


class TestBriefRunAdvancesNovelty(BriefBase):
    def test_record_then_generate_uses_last_run(self):
        """After record_brief_run, a since=None generate resolves `since` to that
        run and demotes items posted before it."""
        from nge.brief import generate
        from nge.timeline import record_brief_run
        import tempfile
        from nge.load import load_all
        with tempfile.TemporaryDirectory() as d:
            con = load_all(os.path.join(d, "t.duckdb"))
            try:
                record_brief_run(con, AS_OF, AS_OF, date(2026, 7, 12))
                b = generate(con, as_of=AS_OF, since=None, generated_at=GEN_AT)
                alex = next(it for it in b.all_items()
                            if "Alexandria" in it.asset_name)
                self.assertEqual(alex.components.novelty, 0.5)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
