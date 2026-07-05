"""Operational event derivation tests (OI-1; DDL-016).

The derivation eval: nge/events.py must reproduce the hand-derived event gold
(data/fixtures/notices/*.expected_events.json) from the notice corpus — chains,
supersession, windows, segment mapping — plus the pure status_at() function and
idempotency. Stdlib assertions; duckdb-gated like the other store tests.
"""
import importlib.util
import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
GOLD = os.path.join(os.path.dirname(__file__), "..", "data", "fixtures",
                    "notices", "cgt_notice_index_2026-06.expected_events.json")


class TestPureFunctions(unittest.TestCase):
    """No DB needed: subject parsing + the status machine."""

    def test_asset_key_facility_phrases(self):
        from nge.events import asset_key
        cases = {
            "Banner Compressor Station Maintenance (BannSEG), July 8 – August 18, 2026":
                ("banner compressor station", "Banner Compressor Station"),
            "UPDATE: Force Majeure – Corinth Compressor Station":
                ("corinth compressor station", "Corinth Compressor Station"),
            "COMPLETED: East Lateral-310 Pigging (4267), Effective ID3 for June 26, 2026":
                ("east lateral-310 pigging (4267)", "East Lateral-310 Pigging (4267)"),
            "CAPACITY POSTING - TIM for July 2, 2026":
                ("capacity posting - tim for july 2, 2026",
                 "CAPACITY POSTING - TIM for July 2, 2026"),
        }
        for subject, expected in cases.items():
            self.assertEqual(asset_key(subject), expected, subject)

    def test_window_parsing_all_observed_shapes(self):
        from nge.events import parse_window
        cases = {
            "X, June 24-25, 2026": (date(2026, 6, 24), date(2026, 6, 25)),
            "X, July 8 – August 18, 2026": (date(2026, 7, 8), date(2026, 8, 18)),
            "X for July 3, 2026, through July 6, 2026": (date(2026, 7, 3), date(2026, 7, 6)),
            "X for Gas Day June 13, 2026": (date(2026, 6, 13), date(2026, 6, 13)),
            "X Unit 505, Effective June 19, 2026": (date(2026, 6, 19), None),
            "no dates here": (None, None),
        }
        for subject, expected in cases.items():
            self.assertEqual(parse_window(subject), expected, subject)

    def test_status_machine(self):
        from nge.events import status_at
        vf, vt = date(2026, 7, 8), date(2026, 7, 10)
        self.assertEqual(status_at(vf, vt, "posted", date(2026, 7, 5)), "planned")
        self.assertEqual(status_at(vf, vt, "posted", date(2026, 7, 9)), "active")
        self.assertEqual(status_at(vf, vt, "posted", date(2026, 7, 11)), "completed")
        # open-ended FM stays active however far out you look
        self.assertEqual(status_at(date(2026, 6, 19), None, "updated",
                                   date(2026, 12, 31)), "active")
        # superseded wins over everything
        self.assertEqual(status_at(vf, vt, "superseded", date(2026, 7, 9)),
                         "superseded")


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class TestDerivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))
        with open(GOLD, encoding="utf-8") as fh:
            cls.gold = json.load(fh)["events"]
        cls.rows = {r[0]: r for r in cls.con.execute("""
            SELECT asset_key, event_type, asset_name, seg_cd, lifecycle_status,
                   valid_from, valid_to, window_source, source_notice_uids,
                   supersedes_event_uid, confidence
            FROM operational_event""").fetchall()}

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_every_gold_event_derived_exactly(self):
        """The derivation eval — every field of every expected event."""
        self.assertEqual(set(self.rows), set(self.gold))
        for key, g in self.gold.items():
            (_, etype, name, seg, life, vf, vt, wsrc, srcs, sup, conf) = self.rows[key]
            self.assertEqual(etype, g["event_type"], key)
            self.assertEqual(name, g["asset_name"], key)
            self.assertEqual(seg, g["seg_cd"], key)
            self.assertEqual(life, g["lifecycle_status"], key)
            self.assertEqual(str(vf) if vf else None, g["valid_from"], key)
            self.assertEqual(str(vt) if vt else None, g["valid_to"], key)
            self.assertEqual(wsrc, g["window_source"], key)
            self.assertEqual(len(srcs), g["n_sources"], key)
            self.assertEqual(bool(sup), bool(g["supersedes_key"]), key)

    def test_supersession_links_correct_target(self):
        revised = self.rows["capacity posting - tim for june 23, 2026#revised"]
        target_uid = revised[9]
        target = self.con.execute(
            "SELECT asset_key, lifecycle_status FROM operational_event"
            " WHERE event_uid = ?", [target_uid]).fetchone()
        self.assertEqual(target[0], "capacity posting - tim for june 23, 2026")
        self.assertEqual(target[1], "superseded")

    def test_chains_aggregate_sources(self):
        self.assertEqual(len(self.rows["east lateral-310 pigging (4267)"][8]), 3)
        self.assertEqual(len(self.rows["corinth compressor station"][8]), 3)

    def test_unmapped_asset_degrades_honestly(self):
        """Corinth has no SEG idiom -> seg_cd NULL, event still fully present."""
        self.assertIsNone(self.rows["corinth compressor station"][3])

    def test_all_source_notices_resolve(self):
        """Referential integrity: every event's sources exist in `notice`."""
        for key, row in self.rows.items():
            for nuid in row[8]:
                self.assertTrue(self.con.execute(
                    "SELECT 1 FROM notice WHERE notice_uid=?", [nuid]).fetchone(),
                    f"{key}: dangling source {nuid}")

    def test_confidence_reflects_window_source(self):
        from nge.events import WINDOW_CONFIDENCE
        for key, row in self.rows.items():
            self.assertEqual(row[10], WINDOW_CONFIDENCE[row[7]], key)

    def test_derivation_idempotent(self):
        from nge.events import derive_events
        before = self.con.execute(
            "SELECT * FROM operational_event ORDER BY event_uid").fetchall()
        derive_events(self.con)
        after = self.con.execute(
            "SELECT * FROM operational_event ORDER BY event_uid").fetchall()
        # system_recorded_at (last col) may differ; compare everything else
        self.assertEqual([r[:-1] for r in before], [r[:-1] for r in after])


if __name__ == "__main__":
    unittest.main(verbosity=2)
