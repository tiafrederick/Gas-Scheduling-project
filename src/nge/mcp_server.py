"""MCP stdio server (Era 3, Phase 2) — the first transport.

A thin, dependency-free adapter that exposes the engine's operations to an MCP client
(a Claude copilot). It speaks the MCP JSON-RPC 2.0 core over newline-delimited stdio —
`initialize` / `tools/list` / `tools/call` — and delegates ALL logic to the tested
transport-agnostic seam (`nge.service`). There is NO business logic here: tools are
generated from the operation registry, a tool call is `service.call_from_arguments`,
and the structured envelope is returned as JSON text content.

Why hand-rolled rather than the `mcp` SDK: the SDK isn't installed in this environment
and can't be exercised, and the project keeps its core stdlib-only. The message handler
(`handle`) is a pure function, fully unit-tested; `serve()` is a trivial stdin loop over
it. Swapping in the official SDK later is a transport-shell change only — the contract
(`nge.contract`), `dispatch`, and `tool_specs` are unmoved, and the conformance test
guarantees any transport returns the same envelopes as the in-process Engine.

Run:  PYTHONPATH=src python3 -m nge.mcp_server        # serves on stdio
"""
from __future__ import annotations

import json
import sys

from .store import DEFAULT_DB

PROTOCOL_VERSION = "2025-06-18"          # echoed back the client's if it sends one
SERVER_INFO = {"name": "nge", "version": "0.1.0"}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(request: dict, engine) -> dict | None:
    """Process one JSON-RPC request. Returns a response dict, or None for a
    notification (no `id`, e.g. `notifications/initialized`). Pure and testable —
    all reasoning is delegated to `nge.service`."""
    from . import service
    method = request.get("method")
    id_ = request.get("id")

    if id_ is None:                       # notification — acknowledged by silence
        return None

    if method == "initialize":
        params = request.get("params") or {}
        return _result(id_, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        tools = [{"name": t["name"], "description": t["description"],
                  "inputSchema": t["input_schema"]} for t in service.tool_specs()]
        return _result(id_, {"tools": tools})

    if method == "tools/call":
        params = request.get("params") or {}
        envelope = service.call_from_arguments(
            engine, params.get("name"), params.get("arguments") or {})
        # MCP convention: tool-level failures are results with isError, not RPC errors.
        return _result(id_, {
            "content": [{"type": "text", "text": json.dumps(envelope)}],
            "isError": envelope.get("error") is not None,
        })

    if method == "ping":
        return _result(id_, {})

    return _rpc_error(id_, -32601, f"method not found: {method}")


def serve(db: str = DEFAULT_DB, stdin=None, stdout=None, engine=None) -> None:
    """Newline-delimited JSON-RPC over stdio. Owns an Engine unless one is injected."""
    from .api import Engine
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    own = engine is None
    engine = engine or Engine(db)
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue                  # skip malformed frames (best-effort transport)
            response = handle(request, engine)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()
    finally:
        if own:
            engine.close()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
