"""Operational Timeline tests (OI-4; OI doc §5).

Window overlap incl. open-ended events, filters, status-at-boundary flips, the
bitemporal golden (a July-2 view must not show the July-3 posting), as-known
CHAIN reconstruction (East Lateral mid-life), as-known supersession (between
the original and REVISED postings), and brief_run bookkeeping.
"""
import importlib.util
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class TimelineBase(unittest.TestCase):
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

    def tl(self, **kw):
        from nge.timeline import timeline
        kw.setdefault("date_from", date(2026, 6, 1))
        kw.setdefault("date_to", date(2026, 7, 31))
        kw.setdefault("as_of", date(2026, 7, 5))
        return timeline(self.con, **kw)

    def names(self, t):
        return [e.asset_name for e in t.entries]


class TestWindowAndFilters(TimelineBase):
    def test_all_eleven_events_in_full_window(self):
        self.assertEqual(len(self.tl().entries), 11)

    def test_open_ended_fm_appears_in_late_window(self):
        """Corinth (valid_to NULL) must intersect a July-only window."""
        t = self.tl(date_from=date(2026, 7, 20), date_to=date(2026, 7, 31))
        self.assertIn("Corinth Compressor Station", self.names(t))

    def test_window_excludes_out_of_range(self):
        t = self.tl(date_from=date(2026, 6, 1), date_to=date(2026, 6, 10))
        names = self.names(t)
        self.assertIn("Paradis Lateral Pipeline", names)      # Jun 8
        self.assertNotIn("Banner Compressor Station", names)  # Jul 8+

    def test_pipe_filter(self):
        self.assertEqual(len(self.tl(pipelines=["CGT"]).entries), 11)
        self.assertEqual(len(self.tl(pipelines=["SESH"]).entries), 0)

    def test_asset_filter_substring_ci(self):
        t = self.tl(assets=["corinth"])
        self.assertEqual(self.names(t), ["Corinth Compressor Station"])

    def test_min_severity(self):
        t = self.tl(min_severity="action")
        names = self.names(t)
        self.assertIn("Corinth Compressor Station", names)          # critical
        self.assertIn("Alexandria and Chicot Compressor Station", names)  # action
        self.assertEqual(len(names), 2)

    def test_ordering_chronological_then_severity(self):
        t = self.tl()
        froms = [e.valid_from for e in t.entries]
        self.assertEqual(froms, sorted(froms, key=lambda d: d or date.max))

    def test_supersession_displayed_both_events(self):
        t = self.tl(assets=["june 23"])
        by_life = {e.lifecycle_status: e for e in t.entries}
        self.assertIn("superseded", by_life)
        self.assertIn("posted", by_life)
        self.assertEqual(by_life["superseded"].superseded_by_uid,
                         by_life["posted"].event_uid)
        self.assertIn("superseded by", by_life["superseded"].line())


class TestStatusBoundaries(TimelineBase):
    def test_alexseg_flips_at_exact_boundaries(self):
        """OI-4.2: planned -> active on valid_from, -> completed after valid_to."""
        def status(d):
            t = self.tl(assets=["Alexandria"], as_of=d)
            return t.entries[0].status_at_as_of
        self.assertEqual(status(date(2026, 7, 7)), "planned")
        self.assertEqual(status(date(2026, 7, 8)), "active")     # first gas day
        self.assertEqual(status(date(2026, 7, 10)), "active")    # last gas day
        self.assertEqual(status(date(2026, 7, 11)), "completed")


class TestBitemporal(TimelineBase):
    def test_july2_view_excludes_july3_posting(self):
        """THE bitemporal golden (OI doc §5.7): the Jul 3-6 capacity posting was
        posted 2026-07-02 07:04 — a midnight-July-2 view must not contain it,
        while the July-2 posting (posted 07-01) and AlexSEG (posted 07-01) must."""
        t = self.tl(date_from=date(2026, 7, 1), date_to=date(2026, 7, 10),
                    as_of=date(2026, 7, 2),
                    as_known=datetime(2026, 7, 2, 0, 0))
        names = self.names(t)
        self.assertNotIn(
            "CAPACITY POSTING - TIM for July 3, 2026, through July 6, 2026",
            names)
        self.assertIn("CAPACITY POSTING - TIM for July 2, 2026", names)
        self.assertIn("Alexandria and Chicot Compressor Station", names)

    def test_as_known_chain_mid_life(self):
        """East Lateral on June 25: the COMPLETED notice (posted 06-26) doesn't
        exist yet — the chain must show lifecycle 'updated', end June 25 (not
        the June 26 completion pin), and only 2 source notices."""
        t = self.tl(date_from=date(2026, 6, 20), date_to=date(2026, 6, 30),
                    as_of=date(2026, 6, 25),
                    as_known=datetime(2026, 6, 25, 0, 0))
        e = next(x for x in t.entries if "East Lateral" in x.asset_name)
        self.assertEqual(e.lifecycle_status, "updated")
        self.assertEqual(e.valid_to, date(2026, 6, 25))
        self.assertEqual(e.n_sources, 2)
        self.assertEqual(e.status_at_as_of, "active")
        # ...and with current knowledge the same event is completed on the 26th
        cur = next(x for x in self.tl(assets=["East Lateral"]).entries)
        self.assertEqual(cur.lifecycle_status, "completed")
        self.assertEqual(cur.valid_to, date(2026, 6, 26))
        self.assertEqual(cur.n_sources, 3)

    def test_as_known_supersession_window(self):
        """Between the original (07:06) and REVISED (07:53) postings on June 22,
        the original is NOT yet superseded; minutes later it is."""
        def lifecycles(known):
            t = self.tl(date_from=date(2026, 6, 23), date_to=date(2026, 6, 23),
                        as_of=date(2026, 6, 22), as_known=known)
            return {e.asset_name: e.lifecycle_status for e in t.entries
                    if "June 23" in e.asset_name}
        before = lifecycles(datetime(2026, 6, 22, 7, 30))
        self.assertEqual(before,
                         {"CAPACITY POSTING - TIM for June 23, 2026": "posted"})
        after = lifecycles(datetime(2026, 6, 22, 8, 0))
        self.assertEqual(after["CAPACITY POSTING - TIM for June 23, 2026"],
                         "superseded")
        self.assertEqual(
            after["CAPACITY POSTING - TIM for June 23, 2026 (REVISED)"],
            "posted")

    def test_as_known_never_mutates_store(self):
        before = self.con.execute(
            "SELECT count(*) FROM operational_event").fetchone()[0]
        self.tl(as_known=datetime(2026, 6, 25, 0, 0))
        after = self.con.execute(
            "SELECT count(*) FROM operational_event").fetchone()[0]
        self.assertEqual(before, after)


class TestBriefRunBookkeeping(TimelineBase):
    def test_record_and_last(self):
        from nge.timeline import last_brief_run, record_brief_run
        self.assertIsNone(last_brief_run(self.con))
        uid = record_brief_run(self.con, date(2026, 7, 5),
                               date(2026, 7, 5), date(2026, 7, 12))
        self.assertTrue(uid)
        self.assertIsNotNone(last_brief_run(self.con))


if __name__ == "__main__":
    unittest.main(verbosity=2)
