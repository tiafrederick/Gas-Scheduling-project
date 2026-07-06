"""Intent registry + executor tests (OI-6.2; OI doc §7.7).

Registry hygiene (every intent has >=2 gold questions, a schema, an executor),
executor correctness for each family, the two honest stubs, and the param
security invariant: malformed / injection / wrong-type params never reach SQL as
text and never raise — they degrade to a graceful ExecResult.
"""
import importlib.util
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
AS_OF = date(2026, 7, 8)


class TestRegistryHygiene(unittest.TestCase):
    def test_eight_intents_each_well_formed(self):
        from nge.intents import REGISTRY
        self.assertEqual(len(REGISTRY), 8)
        names = {i.name for i in REGISTRY}
        self.assertEqual(names, {
            "asset_impact", "events_in_window", "path_between", "point_lookup",
            "contract_exposure", "interconnect_partners", "capacity_at_point",
            "storage_status"})
        for i in REGISTRY:
            self.assertGreaterEqual(len(i.examples), 2, i.name)   # §7.7
            self.assertTrue(callable(i.executor), i.name)
            self.assertIsInstance(i.params_schema, dict, i.name)

    def test_capability_list_names_every_intent(self):
        from nge.intents import REGISTRY, capability_list
        cl = capability_list()
        for i in REGISTRY:
            self.assertIn(i.name, cl)


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class ExecBase(unittest.TestCase):
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

    def run_intent(self, name, params):
        from nge.intents import BY_NAME
        return BY_NAME[name].executor(self.con, params, AS_OF)


class TestExecutors(ExecBase):
    def test_asset_impact_cites_event(self):
        r = self.run_intent("asset_impact", {"asset": "AlexSEG"})
        self.assertTrue(r.ok)
        self.assertIn("SESH", r.text)
        self.assertTrue(any(c.startswith("evt:") for c in r.citations))
        self.assertGreater(r.answer_confidence, 0)

    def test_asset_impact_unknown_lists_known(self):
        r = self.run_intent("asset_impact", {"asset": "NopeSEG"})
        self.assertFalse(r.ok)
        self.assertIn("Known assets", r.text)

    def test_events_in_window(self):
        r = self.run_intent("events_in_window",
                            {"date_from": "2026-07-01", "date_to": "2026-07-15"})
        self.assertIn("OPERATIONAL TIMELINE", r.text)

    def test_events_in_window_bad_date_graceful(self):
        r = self.run_intent("events_in_window", {"date_from": "not-a-date"})
        self.assertFalse(r.ok)

    def test_path_between_cited(self):
        r = self.run_intent("path_between",
                            {"origin": "C000307:519", "destination": "HENRY"})
        self.assertIn("hop", r.text)
        self.assertGreater(r.answer_confidence, 0)

    def test_path_between_unresolvable(self):
        r = self.run_intent("path_between",
                            {"origin": "nowhere", "destination": "HENRY"})
        self.assertFalse(r.ok)

    def test_point_lookup_flags_retired(self):
        r = self.run_intent("point_lookup", {"point": "C000307:4208"})
        self.assertIn("RETIRED", r.text)

    def test_contract_exposure_for_asset(self):
        r = self.run_intent("contract_exposure", {"asset": "AlexSEG"})
        self.assertIn("840245-R1", r.text)

    def test_contract_exposure_lists_all_bp(self):
        r = self.run_intent("contract_exposure", {})
        self.assertIn("BP", r.text)

    def test_interconnect_partners_point(self):
        r = self.run_intent("interconnect_partners", {"point": "C000307:4208"})
        self.assertIn("C001203", r.text)


class TestStubsAreHonest(ExecBase):
    def test_capacity_stub_no_fabrication(self):
        r = self.run_intent("capacity_at_point", {"point": "C000307:4208"})
        self.assertTrue(r.ok)                       # answers, but with zero conf
        self.assertEqual(r.answer_confidence, 0.0)
        self.assertIn("not in the engine", r.text)
        self.assertIn("OAC", r.text)

    def test_storage_stub_no_fabrication(self):
        r = self.run_intent("storage_status", {"facility": "Egan"})
        self.assertEqual(r.answer_confidence, 0.0)
        self.assertIn("shipper", r.text.lower())


class TestParamSecurity(ExecBase):
    NASTY = "'; DROP TABLE point; --"

    def test_injection_strings_never_reach_sql(self):
        from nge.intents import ExecResult
        cases = [
            ("asset_impact", {"asset": self.NASTY}),
            ("point_lookup", {"point": self.NASTY}),
            ("path_between", {"origin": self.NASTY, "destination": self.NASTY}),
            ("contract_exposure", {"asset": self.NASTY}),
            ("interconnect_partners", {"point": self.NASTY}),
            ("events_in_window", {"date_from": self.NASTY}),
        ]
        for name, params in cases:
            r = self.run_intent(name, params)          # must not raise
            self.assertIsInstance(r, ExecResult, name)
        # the table the injection targeted is intact
        self.assertGreater(
            self.con.execute("SELECT count(*) FROM point").fetchone()[0], 0)

    def test_wrong_type_params_dont_crash(self):
        from nge.intents import ExecResult
        for name, params in [("asset_impact", {"asset": 12345}),
                             ("point_lookup", {"point": ["a", "b"]}),
                             ("path_between", {"origin": 1, "destination": 2.0})]:
            r = self.run_intent(name, params)
            self.assertIsInstance(r, ExecResult, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
