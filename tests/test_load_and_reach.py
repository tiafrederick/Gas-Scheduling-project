"""Loader + reachability tests (Phase 2 verification).

Skips cleanly when duckdb is not installed so the stdlib-only tests still run
everywhere (see DDL-011: spikes/tests stay dependency-light).
"""
import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class TestLoadAndReach(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "nge_test.duckdb")
        cls.con = load_all(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_all_core_tables_populated(self):
        from nge.store import table_counts
        counts = table_counts(self.con)
        for table in ("pipeline", "point", "interconnect", "contract_holding",
                      "contract_point", "notice", "capacity_impact_fact",
                      "segment_asset_map"):
            self.assertGreater(counts[table], 0, f"{table} is empty")

    def test_cgt_points_carry_segment_codes(self):
        """CGT's TC eConnects export is the only source with Pipeline Seg Cd;
        confirm it landed (DDL-013 depends on it)."""
        n = self.con.execute(
            "SELECT count(*) FROM point WHERE tsp_ferc_cid='C000307'"
            " AND pipeline_seg_cd IS NOT NULL").fetchone()[0]
        self.assertGreater(n, 100)
        alexdria = self.con.execute(
            "SELECT count(*) FROM point WHERE tsp_ferc_cid='C000307'"
            " AND pipeline_seg_cd='ALEXDRIA'").fetchone()[0]
        self.assertGreaterEqual(alexdria, 10)

    def test_portfolio_pipes_present(self):
        codes = {r[0] for r in self.con.execute(
            "SELECT short_code FROM pipeline WHERE is_portfolio").fetchall()}
        self.assertEqual(codes, {"CGT", "SESH", "SABINE", "EGAN", "BOBCAT"})

    def test_bp_contract_landed_with_term(self):
        row = self.con.execute(
            "SELECT mdq_dth, term_start, term_end FROM contract_holding "
            "WHERE contract_id='840245-R1' AND holder_name ILIKE 'BP %'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 27000)
        self.assertEqual(str(row[1]), "2022-11-01")
        self.assertEqual(str(row[2]), "2027-10-31")

    def test_impact_report_is_cited_end_to_end(self):
        """The v1 slice: AlexSEG -> ALEXDRIA segment points -> SESH via cited
        hops -> BP holdings."""
        from nge.reach import impact_report
        report = impact_report(self.con, "AlexSEG")
        # constraint fact with provenance
        self.assertIn("estimated_capacity_setting", report)
        self.assertIn("2,325,000", report)
        # asset -> segment mapping cited (DDL-013)
        self.assertIn("segment 'ALEXDRIA'", report)
        self.assertIn("C000307:4208D", report)  # segment's own points, not just the asset name
        # cited interconnect hop to SESH (point-level, not whole-pipeline)
        self.assertIn("C000307:4208D -> C001203:83004", report)
        self.assertIn("PORTFOLIO PIPE", report)
        # BP exposure with contract + point citations
        self.assertIn("840245-R1", report)
        self.assertIn("C001203:83101", report)
        # unverified extractions must be flagged, never silent
        self.assertIn("UNVERIFIED", report)

    def test_unknown_asset_fails_soft(self):
        from nge.reach import impact_report
        self.assertIn("No capacity impact facts", impact_report(self.con, "NopeSEG"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
