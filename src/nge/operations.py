"""Operation registry (Era 3, Phase 1) — the single source of truth describing the
Engine's public operations.

Data only: no reasoning, no code generation. It backs boundary input validation now,
and will feed `describe` and per-transport tool generation later (era3 §2, §6). Keeping
the registry COMPLETE (every operation the facade already exposes) rather than a single
`brief` entry is deliberate: an incomplete source-of-truth is itself debt, and listing
methods that already exist is description, not speculation. Only `brief` carries
`serialized: True` today — the others gain payload serializers as their screens land.

`kind`: query (safe, cacheable) | command (mutates — the one write is `brief.record`).
`temporal`: takes an `as_of` gas day. `honors_as_known`: reconstructs history.
"""
from __future__ import annotations

OPERATIONS: dict[str, dict] = {
    "impact": {
        "description": "Cited operational impact assessment for a named asset.",
        "kind": "query", "temporal": True, "honors_as_known": False,
        "params_schema": {"asset": {"type": "string", "required": True}},
    },
    "timeline": {
        "description": "Chronological, bitemporal operational view over a date window.",
        "kind": "query", "temporal": True, "honors_as_known": True,
        "params_schema": {
            "date_from": {"type": "string", "required": True},
            "date_to": {"type": "string", "required": True},
            "pipelines": {"type": "array"}, "assets": {"type": "array"},
            "min_severity": {"type": "string"},
        },
    },
    "brief": {
        "description": "Ranked, cited morning triage brief for a gas day.",
        "kind": "query", "temporal": True, "honors_as_known": False,
        "params_schema": {},          # as_of is the query axis; no positional params
        "serialized": True,
    },
    "brief.record": {
        "description": "Record a brief run (advances novelty). The one write path.",
        "kind": "command", "temporal": True, "honors_as_known": False,
        "params_schema": {},
    },
    "ask": {
        "description": "Natural-language question -> cited answer via the intent registry.",
        "kind": "query", "temporal": True, "honors_as_known": False,
        "params_schema": {"question": {"type": "string", "required": True}},
    },
    "graph.path": {
        "description": "Cited commercial path between two points/hubs/pipelines.",
        "kind": "query", "temporal": False, "honors_as_known": False,
        "params_schema": {"origin": {"type": "string", "required": True},
                          "destination": {"type": "string", "required": True}},
    },
    "graph.neighbors": {
        "description": "A point's declared interconnect neighbours.",
        "kind": "query", "temporal": False, "honors_as_known": False,
        "params_schema": {"uid": {"type": "string", "required": True}},
    },
    "graph.stats": {
        "description": "Graph node/edge counts by kind.",
        "kind": "query", "temporal": False, "honors_as_known": False,
        "params_schema": {},
    },
}


def validate(operation: str, params: dict):
    """(ok: bool, error: dict | None). Rejects unknown operations, missing required
    params, and non-string scalars BEFORE anything reaches an executor or SQL — the
    honest transport-facing gate (era3 §6.7). Defence in depth: executors are
    parameterized regardless. Returns the minimal error-as-data shape."""
    op = OPERATIONS.get(operation)
    if op is None:
        return False, {"code": "unknown_operation",
                       "message": f"no such operation '{operation}'",
                       "operation": operation}
    params = params or {}
    if not isinstance(params, dict):
        return False, {"code": "invalid_params",
                       "message": "params must be an object", "operation": operation}
    for name, spec in op["params_schema"].items():
        if spec.get("required") and name not in params:
            return False, {"code": "invalid_params",
                           "message": f"missing required param '{name}'",
                           "operation": operation}
        if name in params and spec.get("type") == "string" \
                and not isinstance(params[name], str):
            return False, {"code": "invalid_params",
                           "message": f"param '{name}' must be a string",
                           "operation": operation}
    return True, None
