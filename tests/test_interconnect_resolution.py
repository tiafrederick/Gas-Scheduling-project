"""Round-trip interconnect resolution test (verification item, DDL-008/009).

Zero-dependency: runs under `python3 -m unittest` with no external packages.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nge import resolve  # noqa: E402


class TestInterconnectResolution(unittest.TestCase):
    def setUp(self):
        self.catalog, self.points = resolve.load_points()
        self.edges = {e.a_uid: e for e in resolve.resolve(self.points, self.catalog)}

    def test_sesh_cgt_interconnect_reflects_real_posting_asymmetry(self):
        """SESH 'COLUMBIA GULF - DELHI' (83004) declares CGT loc 4208 — CGT's own
        undifferentiated point, since retired (loc_stat_ind='I') in favor of the
        split delivery/receipt pair 4208D/4208R. SESH's posting hasn't been updated
        to reference the split codes, so this is a genuine cross-EBB staleness the
        system must surface, not paper over with a false 1.0 round-trip."""
        fwd = self.edges["C001203:83004"]
        self.assertEqual(fwd.b_uid, "C000307:4208")
        self.assertEqual(fwd.status, "resolved_cid_loc")
        self.assertLess(fwd.confidence, 1.0)

        rev = self.edges["C000307:4208D"]
        self.assertEqual(rev.b_uid, "C001203:83004")
        self.assertEqual(rev.status, "resolved_cid_loc")

    def test_cgt_egan_interconnect_round_trips(self):
        """CGT 'EGAN STOR-ACADIA-REC' (4123) <-> Egan 'COLUMBIA - STORAGE' (45103)."""
        fwd = self.edges["C000307:4123"]
        self.assertEqual(fwd.b_uid, "C000086:45103")
        self.assertEqual(fwd.status, "resolved_roundtrip")
        self.assertEqual(fwd.confidence, 1.0)

        rev = self.edges["C000086:45103"]
        self.assertEqual(rev.b_uid, "C000307:4123")
        self.assertEqual(rev.status, "resolved_roundtrip")

    def test_cgt_sabine_henry_hub_round_trips(self):
        """CGT 'SABINE - HENRY HUB' (519) <-> Sabine 'Columbia Gulf - HH' (11202)."""
        fwd = self.edges["C000307:519"]
        self.assertEqual(fwd.b_uid, "C000830:11202")
        self.assertEqual(fwd.status, "resolved_roundtrip")
        self.assertEqual(fwd.confidence, 1.0)

        rev = self.edges["C000830:11202"]
        self.assertEqual(rev.b_uid, "C000307:519")
        self.assertEqual(rev.status, "resolved_roundtrip")

    def test_na_counterparty_is_not_a_false_positive(self):
        """Rows with no Up/Dn keys must NOT be reported as resolved."""
        for e in self.edges.values():
            if e.status == "unresolved_no_counterparty":
                self.assertIsNone(e.b_uid)

    def test_external_pipes_are_flagged_not_dropped(self):
        """Declared-but-uningested counterparties are surfaced for backlog, not lost."""
        cid_only = [e for e in self.edges.values() if e.status == "resolved_cid_only"]
        self.assertGreater(len(cid_only), 0)
        for e in cid_only:
            self.assertIn(e.b_cid, resolve.KNOWN_PIPELINES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
