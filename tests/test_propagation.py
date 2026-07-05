"""Constraint propagation + impact engine tests (OI-3; DDL-017).

Severity band units, the AlexSEG propagation golden (direction rule, decay,
alternates), the unmapped-FM degradation, citation-chain integrity (every
typed citation resolves against its table), idempotency, and assess().
"""
import importlib.util
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


class TestSeverityModel(unittest.TestCase):
    """Pure units — the constants block is the spec (OI-3.1)."""

    def test_quant_band_edges(self):
        from nge.severity import quant_band
        self.assertEqual(quant_band(0.049), "informational")
        self.assertEqual(quant_band(0.050), "watch")
        self.assertEqual(quant_band(0.149), "watch")
        self.assertEqual(quant_band(0.151), "action")

    def test_alexseg_composite_is_action(self):
        """13.4% cut alone is watch; 'Primary Firm' language floors it to
        action — the calibration case from the design doc."""
        from nge.severity import compute
        sev, components = compute(
            event_type="maintenance", cut_pct=1 - 2_325_000 / 2_683_256,
            affected_services=["Primary Firm", "Secondary Firm", "Interruptible"],
            seg_mapped=True, facts_verified=False)
        self.assertEqual(sev, "action")
        self.assertTrue(any("13.4%" in c and "watch" in c for c in components))
        self.assertTrue(any("Primary Firm" in c for c in components))

    def test_fm_is_critical_regardless_of_numbers(self):
        from nge.severity import compute
        sev, _ = compute("force_majeure", None, [], False, False)
        self.assertEqual(sev, "critical")

    def test_unverified_cap(self):
        """A severity that reaches critical purely from unverified facts is
        capped at action; a verified fact may keep it."""
        from nge.severity import SERVICE_FLOORS, compute
        SERVICE_FLOORS["__test_critical__"] = "critical"
        try:
            sev, comp = compute("maintenance", None, ["__test_critical__"],
                                True, facts_verified=False)
            self.assertEqual(sev, "action")
            self.assertTrue(any("UNVERIFIED cap" in c for c in comp))
            sev, _ = compute("maintenance", None, ["__test_critical__"],
                             True, facts_verified=True)
            self.assertEqual(sev, "critical")
        finally:
            del SERVICE_FLOORS["__test_critical__"]

    def test_mapped_maintenance_floor_and_decay(self):
        from nge.severity import compute, decay
        sev, _ = compute("maintenance", None, [], seg_mapped=True,
                         facts_verified=False)
        self.assertEqual(sev, "watch")   # named job on a mapped segment
        self.assertEqual(decay("action", 1), "watch")
        self.assertEqual(decay("informational", 2), "informational")  # floored


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class PropagationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))
        cls.alexseg_evt = cls.con.execute(
            "SELECT event_uid FROM operational_event WHERE seg_cd='ALEXDRIA'"
        ).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def impacts(self, event_uid, reason=None):
        q = ("SELECT reason_code, subject_uid, hop_distance, severity,"
             " confidence, investigation, citations FROM event_impact"
             " WHERE event_uid = ?")
        args = [event_uid]
        if reason:
            q += " AND reason_code = ?"
            args.append(reason)
        return self.con.execute(q + " ORDER BY reason_code, subject_uid",
                                args).fetchall()


class TestAlexSegPropagationGolden(PropagationBase):
    def test_reason_mix(self):
        from collections import Counter
        mix = Counter(r[0] for r in self.impacts(self.alexseg_evt))
        self.assertEqual(mix["on_constrained_segment"], 14)
        self.assertEqual(mix["storage_service_at_risk"], 1)     # Perryville 4235
        self.assertEqual(mix["downstream_interconnect"], 1)     # SESH via 4208D
        self.assertEqual(mix["contract_at_affected_point"], 1)  # 840245-R1

    def test_direction_rule_backhaul_excludes_pure_receipt_edge(self):
        """Backhaul constraint: 4208D (flow 'out') propagates to SESH 83004;
        4208R (flow 'in') must NOT create a downstream row to 83104 — its
        exposure is surfaced by its own on-segment row instead."""
        down = self.impacts(self.alexseg_evt, "downstream_interconnect")
        subjects = {r[1] for r in down}
        self.assertIn("C001203:83004", subjects)
        self.assertNotIn("C001203:83104", subjects)
        on_seg = {r[1] for r in self.impacts(self.alexseg_evt,
                                             "on_constrained_segment")}
        self.assertIn("C000307:4208R", on_seg)

    def test_severity_decays_one_band_per_hop(self):
        rows = self.impacts(self.alexseg_evt)
        by_hop = {}
        for reason, _s, hop, sev, *_ in rows:
            by_hop.setdefault(hop, set()).add(sev)
        self.assertEqual(by_hop[0], {"action"})
        self.assertEqual(by_hop[1], {"watch"})

    def test_three_investigations_cite_4208R_4208D_and_egan(self):
        """Roadmap OI-3.2 acceptance: >=3 investigations citing 4208R / 4208D /
        the Egan alternate."""
        rows = self.impacts(self.alexseg_evt)
        text = {r[1]: (r[5], list(r[6])) for r in rows}
        # 4208R: its own on-segment investigation + citation
        inv_r, cites_r = text["C000307:4208R"]
        self.assertIn("C000307:4208R", inv_r)
        self.assertIn("point:C000307:4208R", cites_r)
        # 4208D: cited in the downstream row's chain
        inv_d, cites_d = text["C001203:83004"]
        self.assertIn("C000307:4208D", inv_d)
        self.assertIn("point:C000307:4208D", cites_d)
        # Egan alternate: in the contract investigation, cited via the 1.0 edge
        holding = [r for r in rows if r[0] == "contract_at_affected_point"][0]
        self.assertIn("EGAN", holding[5])
        self.assertIn("point:C000086:45103", list(holding[6]))
        self.assertGreaterEqual(len(rows), 3)

    def test_unmapped_fm_degrades_to_pipeline_scope(self):
        evt = self.con.execute("SELECT event_uid FROM operational_event"
                               " WHERE asset_key='corinth compressor station'"
                               ).fetchone()[0]
        rows = self.impacts(evt)
        self.assertEqual(len(rows), 1)
        reason, subject, hop, sev, conf, inv, _ = rows[0]
        self.assertEqual(reason, "on_constrained_pipeline")
        self.assertEqual(subject, "C000307")
        self.assertEqual(sev, "critical")          # FM floors to critical
        self.assertLessEqual(conf, 0.5)            # honestly vague scope
        self.assertIn("segment_asset_map", inv)    # tells the desk how to fix it


class TestIntegrityAndIdempotency(PropagationBase):
    RESOLVERS = {
        "evt": ("operational_event", "event_uid"),
        "fact": ("capacity_impact_fact", "fact_uid"),
        "point": ("point", "point_uid"),
        "ic": ("interconnect", "interconnect_uid"),
        "holding": ("contract_holding", "holding_uid"),
        "pipeline": ("pipeline", "ferc_cid"),
    }

    def test_every_citation_resolves(self):
        """Milestone DoD: every event_impact citation chain resolves to a real
        row. segmap refs check (tsp, seg) pairs; the rest check their table."""
        rows = self.con.execute("SELECT citations FROM event_impact").fetchall()
        checked = 0
        for (cites,) in rows:
            for c in cites:
                kind, _, ref = c.partition(":")
                if kind == "segmap":
                    tsp, _, seg = ref.partition("/")
                    hit = self.con.execute(
                        "SELECT 1 FROM segment_asset_map WHERE tsp_ferc_cid=?"
                        " AND seg_cd=?", [tsp, seg]).fetchone()
                else:
                    table, col = self.RESOLVERS[kind]
                    hit = self.con.execute(
                        f"SELECT 1 FROM {table} WHERE {col}=?", [ref]).fetchone()
                self.assertTrue(hit, f"dangling citation {c}")
                checked += 1
        self.assertGreater(checked, 50)

    def test_confidence_never_exceeds_chain_components(self):
        """No impact row may be more confident than its event."""
        rows = self.con.execute("""
            SELECT ei.confidence, e.confidence FROM event_impact ei
            JOIN operational_event e USING (event_uid)""").fetchall()
        for imp_conf, evt_conf in rows:
            self.assertLessEqual(imp_conf, evt_conf + 1e-9)

    def test_derive_impacts_idempotent(self):
        from nge.propagate import derive_impacts
        before = self.con.execute(
            "SELECT * FROM event_impact ORDER BY impact_uid").fetchall()
        derive_impacts(self.con)
        after = self.con.execute(
            "SELECT * FROM event_impact ORDER BY impact_uid").fetchall()
        self.assertEqual([r[:-1] for r in before], [r[:-1] for r in after])


class TestAssess(PropagationBase):
    def test_alexseg_assessment_golden(self):
        from nge.impact import assess
        a = assess(self.con, "AlexSEG", date(2026, 7, 8))
        self.assertEqual(a.severity, "action")
        self.assertEqual(a.status_at_as_of, "active")
        self.assertEqual(a.confidence, 0.9)
        self.assertTrue(any("Primary Firm" in c for c in a.severity_components))
        text = a.render()
        for needle in ("ACTION", "UNVERIFIED", "ALEXDRIA", "C001203:83004",
                       "840245-R1", "EGAN", "RETIRED"):
            self.assertIn(needle, text)

    def test_assess_respects_as_of(self):
        from nge.impact import assess
        self.assertEqual(assess(self.con, "AlexSEG", date(2026, 7, 5)).status_at_as_of,
                         "planned")
        self.assertEqual(assess(self.con, "AlexSEG", date(2026, 7, 11)).status_at_as_of,
                         "completed")

    def test_assess_by_event_asset_name_too(self):
        from nge.impact import assess
        a = assess(self.con, "Banner Compressor Station", date(2026, 7, 10))
        self.assertIsNotNone(a)
        self.assertEqual(a.status_at_as_of, "active")
        self.assertEqual(a.severity, "watch")   # mapped maintenance, no numbers

    def test_unknown_asset_soft_fail(self):
        from nge.impact import assess
        self.assertIsNone(assess(self.con, "NopeSEG", date(2026, 7, 8)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
