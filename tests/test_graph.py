"""Pipeline Relationship Graph tests (OI-2; DDL-015).

Covers: projection reconciliation vs SQL, known-path goldens (the two 1.0
round-trips + hub reachability + the preserved SESH staleness), confidence
algebra properties (min-composition, no inflation), active/inactive semantics,
lead-edge visibility rules, deterministic builds, and the pinned reach golden.
"""
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "reach_alexseg.txt")


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class GraphTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.load import load_all
        from nge.graph import build
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))
        cls.g = build(cls.con)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()


class TestProjectionReconciliation(GraphTestBase):
    def test_node_counts_match_sql(self):
        s = self.g.stats()
        n_points = self.con.execute("SELECT count(*) FROM point").fetchone()[0]
        n_pipes = self.con.execute("SELECT count(*) FROM pipeline").fetchone()[0]
        n_segs = self.con.execute(
            "SELECT count(DISTINCT tsp_ferc_cid || pipeline_seg_cd) FROM point"
            " WHERE pipeline_seg_cd IS NOT NULL").fetchone()[0]
        n_hubs = self.con.execute("SELECT count(*) FROM market_hub").fetchone()[0]
        self.assertEqual(s["nodes.point"], n_points)
        self.assertEqual(s["nodes.pipeline"] + s["nodes.storage_facility"], n_pipes)
        self.assertEqual(s["nodes.segment"], n_segs)
        self.assertEqual(s["nodes.market_hub"], n_hubs)

    def test_structural_edge_counts_match_sql(self):
        s = self.g.stats()
        n_points = self.con.execute("SELECT count(*) FROM point").fetchone()[0]
        n_on_seg = self.con.execute(
            "SELECT count(*) FROM point WHERE pipeline_seg_cd IS NOT NULL"
        ).fetchone()[0]
        n_members = self.con.execute("SELECT count(*) FROM hub_member").fetchone()[0]
        self.assertEqual(s["edges.of_pipeline"], n_points)
        self.assertEqual(s["edges.on_segment"], n_on_seg)
        self.assertEqual(s["edges.hub_member"], n_members)

    def test_storage_operators_typed_as_storage(self):
        self.assertEqual(self.g.node("C000086").kind, "storage_facility")  # Egan
        self.assertEqual(self.g.node("C001706").kind, "storage_facility")  # Bobcat
        self.assertEqual(self.g.node("C000307").kind, "pipeline")          # CGT


class TestKnownPaths(GraphTestBase):
    def test_cgt_egan_roundtrip_at_full_confidence(self):
        ps = self.g.paths("C000307:4123", "C000086:45103", max_hops=1)
        self.assertTrue(ps)
        self.assertEqual(ps[0].hops, 1)
        self.assertEqual(ps[0].confidence, 1.0)

    def test_cgt_sabine_henry_roundtrip_at_full_confidence(self):
        ps = self.g.paths("C000307:519", "C000830:11202", max_hops=1)
        self.assertTrue(ps)
        self.assertEqual(ps[0].confidence, 1.0)

    def test_henry_hub_one_hop_from_cgt_519(self):
        ps = self.g.paths("C000307:519", "hub:HENRY", max_hops=1)
        self.assertTrue(ps)
        self.assertEqual(ps[0].hops, 1)
        self.assertEqual(ps[0].confidence, 0.95)

    def test_sesh_staleness_preserved_not_inflated(self):
        """SESH 83004 still points at CGT's RETIRED 4208 — the edge exists at
        0.9 (not 1.0), the target is flagged retired, and default (active-only)
        pathfinding refuses to route through it."""
        stale = self.g.paths("C001203:83004", "C000307:4208",
                             max_hops=1, active_only=False)
        self.assertTrue(stale)
        self.assertEqual(stale[0].confidence, 0.9)
        self.assertFalse(self.g.node("C000307:4208").active)
        self.assertIn("[RETIRED]", stale[0].explain())

    def test_explain_is_cited_and_flow_worded(self):
        text = self.g.paths("C000307:4123", "C000086:45103", max_hops=1)[0].explain()
        self.assertIn("bidirectional with", text)   # dir_flo B at CGT 4123
        self.assertIn("cite", text)
        self.assertIn("conf 1.00", text)


class TestConfidenceAlgebra(GraphTestBase):
    def test_path_confidence_is_min_of_edges(self):
        for ps in (self.g.paths("C000307:519", "hub:HENRY", max_hops=3),
                   self.g.paths("C000307:4123", "C000086:45103", max_hops=3)):
            for p in ps:
                self.assertEqual(p.confidence,
                                 min(e.confidence for e in p.edges))

    def test_confidence_never_increases_with_hops(self):
        """Adding hops can only hold or lower path confidence (weakest link)."""
        ps = self.g.paths("C000307:519", "C000830:11202", max_hops=3)
        best_by_hops: dict[int, float] = {}
        for p in ps:
            best_by_hops.setdefault(p.hops, p.confidence)
        hops_sorted = sorted(best_by_hops)
        for shorter, longer in zip(hops_sorted, hops_sorted[1:]):
            self.assertGreaterEqual(best_by_hops[shorter],
                                    best_by_hops[longer])

    def test_hop_bound_respected(self):
        for p in self.g.paths("C000307:519", "hub:HENRY", max_hops=2):
            self.assertLessEqual(p.hops, 2)


class TestLeadEdges(GraphTestBase):
    def test_leads_visible_in_neighbors_but_not_default_paths(self):
        """SESH 83007 declares Transco (C000654) by CID only (0.6 lead): a desk
        knows the counterparty even without its catalog — but leads must not
        route recommended paths at the default threshold."""
        nbrs = self.g.neighbors("C001203:83007", min_conf=0.0)
        lead = [e for e in nbrs if e.dst == "C000654"]
        self.assertTrue(lead)
        self.assertEqual(lead[0].confidence, 0.6)
        self.assertIn("lead", lead[0].note)
        self.assertFalse(self.g.paths("C001203:83007", "C000654"))          # default 0.7
        self.assertTrue(self.g.paths("C001203:83007", "C000654",
                                     min_conf=0.6))                          # explicit opt-in


class TestDeterminism(GraphTestBase):
    def test_two_builds_identical(self):
        from nge.graph import build
        g2 = build(self.con)
        self.assertEqual(self.g.to_json(), g2.to_json())
        self.assertEqual(
            [e.citation for e in self.g.paths("C000307:519", "hub:HENRY")[0].edges],
            [e.citation for e in g2.paths("C000307:519", "hub:HENRY")[0].edges])

    def test_reach_report_matches_golden(self):
        from nge.reach import impact_report
        with open(GOLDEN) as fh:
            golden = fh.read()
        self.assertEqual(impact_report(self.con, "AlexSEG"), golden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
