"""Wire-contract tests (Era 3, Phase 1) — the structured JSON boundary.

Value objects (Citation/Confidence), the envelope, the operation registry +
boundary validation (pure, no DB), and the `brief` payload serialized through the
facade: JSON-serializable (no date/datetime leak), citations + confidence +
provenance + as_of preserved, structurally faithful to the Brief object, dataset
stamped, deterministic. Also: capabilities NOT yet serialized raise a clear error
(honest Phase-1 scope), never silent wrong output.
"""
import importlib.util
import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
AS_OF = date(2026, 7, 5)
GEN_AT = datetime(2026, 7, 5, 6, 30, 0)


class TestValueObjects(unittest.TestCase):
    def test_citation_kind_split_survives_colon_uids(self):
        from nge.contract import citation
        self.assertEqual(citation("evt:abc"), {"kind": "evt", "ref": "evt:abc"})
        # a point ref carries a colon inside the uid — kind is the FIRST segment
        self.assertEqual(citation("point:C000307:4208"),
                         {"kind": "point", "ref": "point:C000307:4208"})

    def test_confidence_bands(self):
        from nge.contract import confidence
        self.assertEqual(confidence(0.95)["band"], "solid")
        self.assertEqual(confidence(0.90)["band"], "solid")
        self.assertEqual(confidence(0.7)["band"], "probable")
        self.assertEqual(confidence(0.5)["band"], "lead")
        self.assertEqual(confidence(0.9, hops=2)["hops"], 2)

    def test_envelope_shape(self):
        from nge.contract import CONTRACT_VERSION, envelope
        e = envelope("brief", date(2026, 7, 5), None, {"snapshot_id": "sig-x"},
                     {"ok": True}, warnings=[{"code": "w"}])
        self.assertEqual(e["contract_version"], CONTRACT_VERSION)
        self.assertEqual(e["operation"], "brief")
        self.assertEqual(e["query"], {"as_of": "2026-07-05", "as_known": None})
        self.assertEqual(e["dataset"]["snapshot_id"], "sig-x")
        self.assertEqual(e["warnings"], [{"code": "w"}])
        self.assertIsNone(e["error"])


class TestOperationRegistry(unittest.TestCase):
    def test_registry_is_complete(self):
        from nge.operations import OPERATIONS
        self.assertEqual(set(OPERATIONS), {
            "impact", "timeline", "brief", "brief.record", "ask",
            "graph.path", "graph.neighbors", "graph.stats"})
        # exactly one write; the rest are safe reads
        cmds = [n for n, o in OPERATIONS.items() if o["kind"] == "command"]
        self.assertEqual(cmds, ["brief.record"])
        # only timeline reconstructs history
        temporal_as_known = [n for n, o in OPERATIONS.items() if o["honors_as_known"]]
        self.assertEqual(temporal_as_known, ["timeline"])

    def test_validate_accepts_good(self):
        from nge.operations import validate
        self.assertEqual(validate("brief", {}), (True, None))
        self.assertEqual(validate("impact", {"asset": "AlexSEG"}), (True, None))

    def test_validate_rejects_unknown_missing_and_wrongtype(self):
        from nge.operations import validate
        self.assertEqual(validate("nope", {})[1]["code"], "unknown_operation")
        self.assertEqual(validate("impact", {})[1]["code"], "invalid_params")
        self.assertEqual(validate("impact", {"asset": 123})[1]["code"], "invalid_params")
        self.assertEqual(validate("impact", ["not", "a", "dict"])[1]["code"],
                         "invalid_params")


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class ContractBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from nge.api import Engine
        from nge.load import load_all
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = load_all(os.path.join(cls.tmp.name, "nge_test.duckdb"))
        cls.e = Engine(con=cls.con)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def brief_dict(self):
        return self.e.brief(as_of=AS_OF, generated_at=GEN_AT).to_dict()


class TestBriefSerialization(ContractBase):
    def test_json_serializable_and_envelope(self):
        d = self.brief_dict()
        json.dumps(d)                                 # no date/datetime leak
        self.assertEqual(d["contract_version"], 1)
        self.assertEqual(d["operation"], "brief")
        self.assertEqual(d["query"]["as_of"], "2026-07-05")
        self.assertIsNone(d["query"]["as_known"])
        self.assertTrue(d["dataset"]["snapshot_id"].startswith("sig-"))
        self.assertEqual(d["dataset"]["boundary"], "public_ferc_postings")

    def test_sections_and_item_shape(self):
        d = self.brief_dict()
        secs = d["result"]["sections"]
        self.assertEqual(list(secs), ["critical", "action", "watch", "informational"])
        alex = next(it for it in secs["action"] if "Alexandria" in it["asset_name"])
        self.assertEqual(alex["severity"], "action")
        self.assertEqual(alex["confidence"], {"value": 0.9, "band": "solid"})
        self.assertEqual(alex["window"], {"from": "2026-07-08", "to": "2026-07-10"})
        self.assertIn("severity_weight", alex["score_components"])
        self.assertTrue(alex["citations"])
        for c in alex["citations"]:                    # every citation is a value object
            self.assertEqual(set(c), {"kind", "ref"})

    def test_structurally_faithful_to_brief_object(self):
        """to_dict carries the SAME facts as the Brief object (and thus render)."""
        b = self.e.brief(as_of=AS_OF, generated_at=GEN_AT).brief
        d = self.brief_dict()
        for sev in ("critical", "action", "watch", "informational"):
            want = [it.asset_name for it in b.sections.get(sev, [])]
            got = [it["asset_name"] for it in d["result"]["sections"][sev]]
            self.assertEqual(got, want, sev)
        alex_item = next(it for it in b.sections["action"] if "Alexandria" in it.asset_name)
        alex_dict = next(it for it in d["result"]["sections"]["action"]
                         if "Alexandria" in it["asset_name"])
        self.assertEqual(alex_dict["score"], alex_item.score)

    def test_data_quality_and_provenance_preserved(self):
        d = self.brief_dict()
        stale = next(n for n in d["result"]["data_quality"] if "4208" in n["statement"])
        self.assertIn("RETIRED", stale["statement"])
        refs = [c["ref"] for c in stale["citations"]]
        self.assertIn("point:C000307:4208", refs)
        self.assertIn("ic:35b371eb182cf768", refs)

    def test_unconfirmed_critical_flag(self):
        d = self.brief_dict()
        corinth = d["result"]["sections"]["critical"][0]
        self.assertTrue(corinth["unconfirmed"])
        self.assertEqual(corinth["severity"], "critical")

    def test_deterministic(self):
        self.assertEqual(self.brief_dict(), self.brief_dict())

    def test_dataset_stamped_and_stable(self):
        from nge import contract
        d = self.brief_dict()
        self.assertEqual(d["dataset"], self.e.dataset())          # cached, stamped
        self.assertEqual(self.e.dataset(), contract.dataset(self.con))  # same signature


class TestUnserializedCapabilitiesFailLoud(ContractBase):
    def test_ask_to_dict_not_yet_supported(self):
        """`ask` is intentionally NOT serialized (a copilot is itself the NL layer,
        era3 §1); its to_dict must fail loud, never emit a guessed shape."""
        with self.assertRaises(NotImplementedError):
            self.e.ask("anything", as_of=date(2026, 7, 8)).to_dict()


if __name__ == "__main__":
    unittest.main(verbosity=2)
