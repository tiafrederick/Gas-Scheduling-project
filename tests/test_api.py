"""Service facade tests (OI-7; OI doc §1.4, §8.1; DDL-020).

The facade is the public contract, so this suite exercises ALL SIX capabilities
through `nge.api.Engine` alone — never the capability modules directly. It pins
the uniform bitemporal contract (as_known threaded on timeline, rejected
elsewhere), the atemporal graph envelope, graceful bad-input handling, the
golden-brief routed through the facade (proving the facade changes no output),
and the §8.1 AlexSEG end-to-end scenario. Plus an architectural guard that every
CLI delegates to the facade rather than re-implementing capability logic.
"""
import importlib.util
import os
import re
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
SRC = os.path.join(os.path.dirname(__file__), "..", "src", "nge")
GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "brief_2026-07-05.md")
AS_OF = date(2026, 7, 5)


class TestFacadeImportsWithoutDuckdb(unittest.TestCase):
    def test_response_types_and_capabilities_available(self):
        from nge.api import CAPABILITIES, ApiResponse, Engine
        self.assertEqual(len(CAPABILITIES), 7)          # 6 capabilities (graph = 3 views)
        self.assertTrue(hasattr(Engine, "impact"))
        self.assertTrue(issubclass(ApiResponse, object))


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class FacadeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.api import Engine
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))
        cls.e = Engine(con=cls.con)                     # share the loaded connection

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()


class TestEveryCapabilityThroughFacade(FacadeBase):
    def test_impact(self):
        r = self.e.impact("AlexSEG", as_of=date(2026, 7, 8))
        self.assertEqual(r.capability, "impact")
        self.assertEqual(r.as_of, date(2026, 7, 8))
        self.assertIn("IMPACT ASSESSMENT", r.render())

    def test_timeline(self):
        r = self.e.timeline(date(2026, 7, 1), date(2026, 7, 15), as_of=AS_OF)
        self.assertEqual(r.capability, "timeline")
        self.assertIn("OPERATIONAL TIMELINE", r.render())

    def test_brief(self):
        r = self.e.brief(as_of=AS_OF, generated_at=datetime(2026, 7, 5, 6, 30))
        self.assertEqual(r.capability, "brief")
        self.assertIn("# Morning Brief", r.render())

    def test_ask(self):
        r = self.e.ask("Look up point C000307:4208.", as_of=AS_OF)
        self.assertEqual(r.capability, "ask")
        self.assertIn("RETIRED", r.render())

    def test_path(self):
        r = self.e.path("C000307:519", "hub:HENRY")
        self.assertEqual(r.capability, "path")
        self.assertIsNone(r.as_of)                      # atemporal
        self.assertIn("hop", r.render())

    def test_neighbors(self):
        r = self.e.neighbors("C000307:519")
        self.assertIsNone(r.as_of)
        self.assertIn("C000307:519", r.render())

    def test_graph_stats(self):
        r = self.e.graph_stats()
        self.assertIsNone(r.as_of)
        self.assertGreater(r.stats["nodes.point"], 0)


class TestBitemporalContract(FacadeBase):
    def test_timeline_threads_as_known(self):
        """The July-2 view must exclude the Jul-3-6 posting (posted 07:04)."""
        r = self.e.timeline(date(2026, 7, 1), date(2026, 7, 10),
                            as_known=datetime(2026, 7, 2, 0, 0))
        self.assertEqual(r.as_known, datetime(2026, 7, 2, 0, 0))
        self.assertNotIn("July 3, 2026, through July 6", r.render())

    def test_impact_brief_ask_reject_as_known(self):
        from nge.api import AsKnownUnsupported
        k = datetime(2026, 7, 2, 0, 0)
        with self.assertRaises(AsKnownUnsupported):
            self.e.impact("AlexSEG", as_known=k)
        with self.assertRaises(AsKnownUnsupported):
            self.e.brief(as_of=AS_OF, as_known=k)
        with self.assertRaises(AsKnownUnsupported):
            self.e.ask("anything", as_known=k)


class TestGracefulBadInput(FacadeBase):
    def test_unknown_path_node_no_crash(self):
        r = self.e.path("C000307:519", "NOPE:9")
        self.assertIsNotNone(r.error)
        self.assertIn("Unknown destination", r.render())

    def test_unknown_neighbor_no_crash(self):
        r = self.e.neighbors("NOPE:9")
        self.assertIn("Unknown graph node", r.render())


class TestFacadePreservesOutput(FacadeBase):
    def test_brief_through_facade_matches_golden(self):
        r = self.e.brief(as_of=AS_OF, since=None,
                        generated_at=datetime(2026, 7, 5, 6, 30, 0))
        with open(GOLDEN, encoding="utf-8") as fh:
            self.assertEqual(r.render(explain=False), fh.read())

    def test_record_requires_writable_engine(self):
        with self.assertRaises(ValueError):
            self.e.brief(as_of=AS_OF, record=True)      # shared con is read-only-intent


class TestEndToEndAlexSEG(FacadeBase):
    """OI doc §8.1: the golden cross-pipeline scenario, entirely through the facade."""
    def test_scenario(self):
        as_of = date(2026, 7, 8)
        # 1. Impact: AlexSEG -> action severity, SESH exposure, BP contract, cited.
        imp = self.e.impact("AlexSEG", as_of=as_of)
        a = imp.assessment
        self.assertEqual(a.severity, "action")
        self.assertIn("contract_at_affected_point", a.impacts_by_reason)
        self.assertTrue(any(c.startswith("holding:") for c in a.citations))
        # 2. Timeline: AlexSEG is planned/active in the window.
        tl = self.e.timeline(date(2026, 7, 1), date(2026, 7, 15), as_of=as_of)
        self.assertTrue(any("Alexandria" in e.asset_name for e in tl.timeline.entries))
        # 3. NL query routes the vision question and answers with SESH + citations.
        ans = self.e.ask("How does the AlexSEG maintenance affect SESH?", as_of=as_of)
        self.assertEqual(ans.answer.intent, "asset_impact")
        self.assertIn("SESH", ans.render())
        self.assertTrue(ans.answer.citations)
        # 4. Graph: the cited CGT<->Egan roundtrip the impact alternates rely on.
        pth = self.e.path("C000307:4123", "C000086:45103")
        self.assertEqual(pth.paths[0].confidence, 1.0)
        # 5. Brief: AlexSEG surfaces in the Action section for the gas week.
        br = self.e.brief(as_of=AS_OF, generated_at=datetime(2026, 7, 5, 6, 30))
        self.assertIn("Alexandria and Chicot", br.render())


class TestCliDelegatesToFacade(unittest.TestCase):
    """Architectural guard: each CLI is a thin wrapper — its main() goes through
    nge.api.Engine and does not open its own connection or call capability
    functions directly."""
    CLIS = ["impact", "timeline", "brief", "ask", "graphq"]

    def test_clis_route_through_engine(self):
        for mod in self.CLIS:
            with open(os.path.join(SRC, f"{mod}.py"), encoding="utf-8") as fh:
                src = fh.read()
            main = src[src.index("def main("):]
            self.assertIn("Engine", main, f"{mod}.main must use the facade")
            self.assertNotIn("duckdb.connect", main,
                             f"{mod}.main must not open its own connection")


if __name__ == "__main__":
    unittest.main(verbosity=2)
