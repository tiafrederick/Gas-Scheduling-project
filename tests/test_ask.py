"""NL query router tests (OI-6.3; OI doc §7.7).

The ~20-question router gold set runs against the FALLBACK router in CI always
(the LLM router only when a key is present). Plus: the refusal golden, the
two-confidence-channel contract, the LLM tool-use routing logic exercised with a
fake client (no key needed), and the §8.1 DoD question answered via both routers.
"""
import importlib.util
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
AS_OF = date(2026, 7, 8)

# (question, expected_intent) — expected_intent None means out-of-scope refusal.
ROUTER_GOLD = [
    ("How does the AlexSEG maintenance affect SESH?", "asset_impact"),
    ("What does the Alexandria compressor station outage touch?", "asset_impact"),
    ("Assess the impact of the Corinth force majeure.", "asset_impact"),
    ("What's happening on CGT between 2026-07-01 and 2026-07-15?", "events_in_window"),
    ("Show me the events from 2026-07-05 to 2026-07-12.", "events_in_window"),
    ("What operational events are scheduled 2026-06-20 to 2026-06-30?", "events_in_window"),
    ("How does gas get from C000307:519 to HENRY?", "path_between"),
    ("Show the path between C000307:4123 and C000086:45103.", "path_between"),
    ("Look up point C000307:4208.", "point_lookup"),
    ("What is point C000307:519?", "point_lookup"),
    ("What BP contracts are exposed by the AlexSEG maintenance?", "contract_exposure"),
    ("Show me BP's firm contracts.", "contract_exposure"),
    ("Who does CGT connect to at C000307:4208?", "interconnect_partners"),
    ("List CGT's counterparty pipelines.", "interconnect_partners"),
    ("What's the available capacity at C000307:4208 tomorrow?", "capacity_at_point"),
    ("How much operating capacity is on C000307:519 today?", "capacity_at_point"),
    ("What's the Egan storage balance?", "storage_status"),
    ("Show me the Bobcat storage inventory.", "storage_status"),
    ("What will Henry Hub basis do tomorrow?", None),
    ("Should I buy gas next week?", None),
]


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class RouterBase(unittest.TestCase):
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


class TestFallbackRouterGold(RouterBase):
    def test_gold_set_routes(self):
        from nge.ask import route_fallback
        misses = []
        for q, expected in ROUTER_GOLD:
            got, _params, _conf = route_fallback(self.con, q)
            if got != expected:
                misses.append((q, expected, got))
        self.assertEqual(misses, [], f"router misses: {misses}")

    def test_every_intent_first_example_routes_to_itself(self):
        from nge.ask import route_fallback
        from nge.intents import REGISTRY
        for i in REGISTRY:
            got, _p, _c = route_fallback(self.con, i.examples[0])
            self.assertEqual(got, i.name, f"{i.name}: {i.examples[0]!r} -> {got}")


class TestRefusalGolden(RouterBase):
    def test_out_of_scope_lists_capabilities(self):
        from nge.ask import ask
        a = ask(self.con, "What will Henry Hub basis do tomorrow?", AS_OF,
                router="fallback")
        self.assertTrue(a.out_of_scope)
        text = a.render()
        self.assertIn("outside what this engine can answer", text)
        # the refusal must enumerate what CAN be asked (never a guess)
        for name in ("asset_impact", "path_between", "storage_status"):
            self.assertIn(name, text)


class TestConfidenceChannels(RouterBase):
    def test_routing_and_answer_confidence_are_separate(self):
        from nge.ask import ask
        a = ask(self.con, "How does the AlexSEG maintenance affect SESH?", AS_OF,
                router="fallback")
        self.assertEqual(a.intent, "asset_impact")
        self.assertGreater(a.routing_confidence, 0)     # understood the question
        self.assertGreater(a.answer_confidence, 0)      # executor's own confidence
        # they are distinct fields, not one blended number
        self.assertIsNot(a.routing_confidence, a.answer_confidence)


class TestDoDQuestionBothRouters(RouterBase):
    Q = "How does the AlexSEG maintenance affect SESH?"

    def test_fallback_answers_with_citations(self):
        from nge.ask import ask
        a = ask(self.con, self.Q, AS_OF, router="fallback")
        self.assertEqual(a.intent, "asset_impact")
        self.assertIn("SESH", a.rendered)
        self.assertTrue(a.citations)

    def test_llm_router_via_fake_client(self):
        """Exercise route_llm's tool-call parsing deterministically, no key."""
        from nge.ask import route_llm
        from nge.llm import LLMResponse, ToolCall

        class FakeClient:
            def __init__(self, resp):
                self._resp = resp
            def complete(self, **kw):
                return self._resp

        ok = FakeClient(LLMResponse(text="", tool_calls=[ToolCall(
            "route", {"intent": "asset_impact", "params": {"asset": "AlexSEG"},
                      "confidence": 0.92})]))
        intent, params, conf = route_llm(ok, self.Q)
        self.assertEqual(intent, "asset_impact")
        self.assertEqual(params, {"asset": "AlexSEG"})
        self.assertAlmostEqual(conf, 0.92)

    def test_llm_router_out_of_scope_and_refusal(self):
        from nge.ask import OUT_OF_SCOPE, route_llm
        from nge.llm import LLMResponse, ToolCall

        class Fake:
            def __init__(self, resp):
                self._resp = resp
            def complete(self, **kw):
                return self._resp

        oos = Fake(LLMResponse(tool_calls=[ToolCall("route", {"intent": OUT_OF_SCOPE})]))
        self.assertEqual(route_llm(oos, "price forecast?")[0], OUT_OF_SCOPE)
        refused = Fake(LLMResponse(refused=True))
        self.assertIsNone(route_llm(refused, "anything")[0])


class TestLLMClientContract(unittest.TestCase):
    def test_imports_and_gates_without_key(self):
        """nge.llm must import with no SDK/key; has_key() gates LLM-path tests."""
        from nge.llm import DEFAULT_MODEL, LLMClient, LLMResponse
        self.assertIsInstance(DEFAULT_MODEL, str)
        self.assertIsInstance(LLMClient.has_key(), bool)
        self.assertIsInstance(LLMResponse(text="hi"), LLMResponse)

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb") is not None
        and __import__("nge.llm", fromlist=["LLMClient"]).LLMClient.available(),
        "no ANTHROPIC_API_KEY / SDK — LLM path skipped (no-LLM is the CI default)")
    def test_llm_router_real_key_matches_fallback(self):  # pragma: no cover
        import tempfile
        from nge.ask import route_fallback, route_llm
        from nge.llm import LLMClient
        from nge.load import load_all
        with tempfile.TemporaryDirectory() as d:
            con = load_all(os.path.join(d, "t.duckdb"))
            try:
                agree = 0
                for q, expected in ROUTER_GOLD:
                    got, _p, _c = route_llm(LLMClient(), q)
                    fb, _, _ = route_fallback(con, q)
                    if (got or "out_of_scope") == (expected or "out_of_scope") \
                            or got == fb:
                        agree += 1
                self.assertGreaterEqual(agree, int(0.8 * len(ROUTER_GOLD)))
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
