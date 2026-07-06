"""Transport-agnostic service layer (Era 3, Phase 2) — the seam every transport calls.

`dispatch(engine, operation, params, as_of, as_known)` validates params against the
operation registry, invokes the right `Engine` method, and returns the structured
envelope (or an error envelope). It NEVER raises and NEVER leaks an internal exception:
`AsKnownUnsupported`, bad dates, and unexpected errors all become error-as-data. A
transport adapter (MCP, later HTTP) is a thin wrapper over this — it decodes a request
into (operation, arguments), calls `call_from_arguments`, and encodes the returned dict.

`tool_specs()` projects the registry into MCP-tool / JSON-Schema shapes, so tools are
GENERATED, not hand-written — one registry, N transports.

Conformance guarantee (tested): `dispatch(engine, op, params, ...)` equals the matching
`Engine` method's `.to_dict()`. The adapters add no shape of their own.
"""
from __future__ import annotations

from datetime import date, datetime

from . import contract, operations
from .api import AsKnownUnsupported


def _invoke(engine, operation, params, as_of, as_known):
    if operation == "brief":
        return engine.brief(as_of=as_of, as_known=as_known)
    if operation == "impact":
        return engine.impact(params["asset"], as_of=as_of, as_known=as_known)
    if operation == "timeline":
        return engine.timeline(
            date.fromisoformat(params["date_from"]),
            date.fromisoformat(params["date_to"]),
            pipelines=params.get("pipelines"), assets=params.get("assets"),
            min_severity=params.get("min_severity"), as_of=as_of, as_known=as_known)
    if operation == "graph.path":
        return engine.path(params["origin"], params["destination"],
                           max_hops=params.get("max_hops", 4),
                           min_conf=params.get("min_conf", 0.7),
                           include_inactive=params.get("include_inactive", False))
    if operation == "graph.neighbors":
        return engine.neighbors(params["uid"])
    if operation == "graph.stats":
        return engine.graph_stats()
    raise KeyError(operation)                       # pragma: no cover (registry-gated)


def dispatch(engine, operation: str, params: dict | None = None,
             as_of: date | None = None, as_known: datetime | None = None) -> dict:
    params = params or {}
    op = operations.OPERATIONS.get(operation)
    if op is None or not op.get("serialized") or op["kind"] == "command":
        return _err(engine, operation, as_of, as_known, "unknown_operation",
                    f"'{operation}' is not an exposed operation")
    ok, err = operations.validate(operation, params)
    if not ok:
        return contract.envelope(operation, as_of, as_known, _safe_dataset(engine),
                                 result=None, error=err)
    try:
        return _invoke(engine, operation, params, as_of, as_known).to_dict()
    except AsKnownUnsupported as e:
        return _err(engine, operation, as_of, as_known, "as_known_unsupported", str(e))
    except ValueError as e:                         # e.g. malformed date param
        return _err(engine, operation, as_of, as_known, "invalid_params", str(e))
    except Exception as e:                          # never leak an internal exception
        return _err(engine, operation, as_of, as_known, "internal_error", str(e))


def call_from_arguments(engine, operation: str, arguments: dict | None) -> dict:
    """Transport helper: split the bitemporal axes (`as_of`, `as_known`) out of a flat
    tool-arguments dict, parse them, and dispatch. Bad dates -> invalid_params."""
    args = dict(arguments or {})
    raw_of, raw_known = args.pop("as_of", None), args.pop("as_known", None)
    try:
        as_of = date.fromisoformat(raw_of) if raw_of else None
        as_known = datetime.fromisoformat(raw_known) if raw_known else None
    except ValueError as e:
        return _err(engine, operation, None, None, "invalid_params",
                    f"bad date argument: {e}")
    return dispatch(engine, operation, args, as_of=as_of, as_known=as_known)


def _err(engine, operation, as_of, as_known, code, message) -> dict:
    return contract.envelope(operation, as_of, as_known, _safe_dataset(engine),
                             result=None,
                             error=contract.error(code, message, operation))


def _safe_dataset(engine) -> dict:
    try:
        return engine.dataset()
    except Exception:                               # pragma: no cover
        return {}


def tool_specs() -> list[dict]:
    """Registry -> transport tool specs (name, description, JSON-Schema input) for
    every exposed operation. The bitemporal axes are added as input fields where the
    operation supports them."""
    specs = []
    for name in operations.exposed():
        op = operations.OPERATIONS[name]
        props: dict = {}
        required: list[str] = []
        for pname, spec in op["params_schema"].items():
            t = spec.get("type", "string")
            schema = {"type": t}
            if t == "array":
                schema["items"] = {"type": "string"}
            props[pname] = schema
            if spec.get("required"):
                required.append(pname)
        if op["temporal"]:
            props["as_of"] = {"type": "string",
                              "description": "gas day YYYY-MM-DD (default: today)"}
        if op["honors_as_known"]:
            props["as_known"] = {"type": "string",
                                 "description": "knowledge timestamp ISO-8601"}
        specs.append({"name": name, "description": op["description"],
                      "input_schema": {"type": "object", "properties": props,
                                       "required": required}})
    return specs
