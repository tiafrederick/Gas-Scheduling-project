"""Transport tests (Era 3, Phase 2) — the service seam + the MCP stdio adapter.

The conformance guarantee (era3 §9 risk #5): a transport adds no shape — every
`dispatch()` result equals the matching Engine method's `.to_dict()`. Plus: the
error taxonomy, `tool_specs()` generation, and the MCP JSON-RPC handler / serve loop
(pure, dependency-free), which delegates everything to the tested seam.
"""
import importlib.util
import io
import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
AS_OF = date(2026, 7, 8)


class TestToolSpecs(unittest.TestCase):
    def test_exposes_deterministic_ops_only(self):
        from nge.service import tool_specs
        names = [t["name"] for t in tool_specs()]
        self.assertEqual(set(names), {"impact", "timeline", "brief",
                                      "graph.path", "graph.neighbors", "graph.stats"})
        self.assertNotIn("ask", names)          # copilot IS the NL layer
        self.assertNotIn("brief.record", names)  # the write is not exposed

    def test_schema_includes_bitemporal_axes_correctly(self):
        from nge.service import tool_specs
        specs = {t["name"]: t for t in tool_specs()}
        # temporal ops carry as_of; only timeline carries as_known
        self.assertIn("as_of", specs["impact"]["input_schema"]["properties"])
        self.assertIn("as_known", specs["timeline"]["input_schema"]["properties"])
        self.assertNotIn("as_known", specs["impact"]["input_schema"]["properties"])
        # atemporal graph op carries neither
        self.assertNotIn("as_of", specs["graph.stats"]["input_schema"]["properties"])
        self.assertEqual(specs["impact"]["input_schema"]["required"], ["asset"])


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class TransportBase(unittest.TestCase):
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


class TestDispatchConformance(TransportBase):
    """dispatch(...) == Engine.<method>(...).to_dict() — the adapter adds no shape."""

    def test_impact(self):
        from nge.service import dispatch
        self.assertEqual(
            dispatch(self.e, "impact", {"asset": "AlexSEG"}, as_of=AS_OF),
            self.e.impact("AlexSEG", as_of=AS_OF).to_dict())

    def test_timeline(self):
        from nge.service import dispatch
        self.assertEqual(
            dispatch(self.e, "timeline",
                     {"date_from": "2026-07-01", "date_to": "2026-07-15"},
                     as_of=date(2026, 7, 5)),
            self.e.timeline(date(2026, 7, 1), date(2026, 7, 15),
                            as_of=date(2026, 7, 5)).to_dict())

    def test_brief(self):
        from nge.service import dispatch
        self.assertEqual(dispatch(self.e, "brief", as_of=date(2026, 7, 5))["operation"],
                         "brief")

    def test_graph_all_three(self):
        from nge.service import dispatch
        self.assertEqual(
            dispatch(self.e, "graph.path",
                     {"origin": "C000307:519", "destination": "hub:HENRY"}),
            self.e.path("C000307:519", "hub:HENRY").to_dict())
        self.assertEqual(dispatch(self.e, "graph.neighbors", {"uid": "C000307:519"}),
                         self.e.neighbors("C000307:519").to_dict())
        self.assertEqual(dispatch(self.e, "graph.stats"),
                         self.e.graph_stats().to_dict())


class TestDispatchErrors(TransportBase):
    def test_error_taxonomy(self):
        from nge.service import call_from_arguments, dispatch
        self.assertEqual(dispatch(self.e, "ask", {"question": "x"})["error"]["code"],
                         "unknown_operation")             # not exposed
        self.assertEqual(dispatch(self.e, "brief.record")["error"]["code"],
                         "unknown_operation")             # the write is not exposed
        self.assertEqual(dispatch(self.e, "impact", {})["error"]["code"],
                         "invalid_params")                # missing required asset
        self.assertEqual(dispatch(self.e, "impact", {"asset": 5})["error"]["code"],
                         "invalid_params")                # wrong type
        self.assertEqual(dispatch(self.e, "impact", {"asset": "Nope"})["error"]["code"],
                         "unknown_asset")                 # graceful domain miss
        self.assertEqual(
            call_from_arguments(self.e, "impact",
                                {"asset": "AlexSEG", "as_known": "2026-07-02T00:00"}
                                )["error"]["code"],
            "as_known_unsupported")
        self.assertEqual(
            dispatch(self.e, "graph.path",
                     {"origin": "C000307:519", "destination": "NOPE:1"})["error"]["code"],
            "unknown_node")

    def test_bad_date_argument(self):
        from nge.service import call_from_arguments
        r = call_from_arguments(self.e, "impact",
                                {"asset": "AlexSEG", "as_of": "not-a-date"})
        self.assertEqual(r["error"]["code"], "invalid_params")

    def test_never_raises(self):
        """Any exposed op with any junk params returns an envelope, never raises."""
        from nge.service import dispatch
        for op in ("impact", "timeline", "graph.path", "graph.neighbors", "brief"):
            r = dispatch(self.e, op, {"garbage": object()})   # non-JSON junk
            self.assertIn("error", r)


class TestMcpHandler(TransportBase):
    def call(self, method, params=None, id_=1):
        from nge.mcp_server import handle
        req = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            req["params"] = params
        return handle(req, self.e)

    def test_initialize_echoes_protocol(self):
        r = self.call("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["serverInfo"]["name"], "nge")

    def test_notification_gets_no_response(self):
        from nge.mcp_server import handle
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                 self.e))

    def test_tools_list_matches_specs(self):
        from nge.service import tool_specs
        r = self.call("tools/list")
        got = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(got, {t["name"] for t in tool_specs()})
        self.assertIn("inputSchema", r["result"]["tools"][0])   # MCP camelCase

    def test_tools_call_returns_envelope_json(self):
        from nge.service import dispatch
        r = self.call("tools/call",
                      {"name": "impact", "arguments": {"asset": "AlexSEG",
                                                       "as_of": "2026-07-08"}})
        self.assertFalse(r["result"]["isError"])
        envelope = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(envelope, dispatch(self.e, "impact", {"asset": "AlexSEG"},
                                            as_of=AS_OF))

    def test_tools_call_error_sets_iserror(self):
        r = self.call("tools/call", {"name": "impact", "arguments": {"asset": "Nope"}})
        self.assertTrue(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["error"]["code"],
                         "unknown_asset")

    def test_unknown_method_is_rpc_error(self):
        r = self.call("does/not/exist")
        self.assertEqual(r["error"]["code"], -32601)


class TestServeLoop(TransportBase):
    def test_stdio_roundtrip(self):
        from nge.mcp_server import serve
        inp = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "brief", "arguments": {"as_of": "2026-07-05"}}}),
        ]) + "\n"
        out = io.StringIO()
        serve(stdin=io.StringIO(inp), stdout=out, engine=self.e)
        lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
        self.assertEqual(len(lines), 2)                 # init + call; notification silent
        self.assertEqual(lines[0]["id"], 1)
        env = json.loads(lines[1]["result"]["content"][0]["text"])
        self.assertEqual(env["operation"], "brief")


if __name__ == "__main__":
    unittest.main(verbosity=2)
