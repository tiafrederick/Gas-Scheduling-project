"""The service facade (OI-7; DDL-020) — the ONE public interface to the engine.

Everything an application (a UI, an HTTP server, an MCP tool, a notebook) needs
goes through `Engine`. The capability modules (impact, timeline, brief, ask,
graph, propagate) are the deterministic core; this module is the stable contract
in front of them, so their internals can be refactored without breaking callers.

Design (DDL-020):
  * `Engine` owns the DuckDB connection — opened read-only by default, one place
    that enforces the read-only invariant and the connection lifecycle. Use it as
    a context manager (`with Engine() as e: ...`) or call `.close()`.
  * ONE method per capability, each returning a typed `*Response` that carries a
    uniform envelope — `capability`, `as_of`, `as_known` — plus `.render()`.
    `render()` delegates to the wrapped capability object, so the facade changes
    NO externally-observable output (goldens are unmoved).
  * Uniform bitemporal contract: temporal methods take `as_of` (gas day; default
    today) and optional `as_known` (timestamp). `as_known` is threaded where a
    capability actually reconstructs history (timeline) and REJECTED with
    `AsKnownUnsupported` where it doesn't — never silently ignored, because a
    current-knowledge answer under a historical query would be a correctness lie.
  * The relationship graph is a structural projection, not a gas-day view, so its
    methods (`path`, `neighbors`, `graph_stats`) are atemporal (`as_of=None`).

`import nge.api` works with no duckdb installed (the import is lazy in `Engine`)
so the response dataclasses are usable anywhere; a live query needs a store.

Run (the six capabilities, all via this facade):
    from nge.api import Engine
    with Engine() as e:
        print(e.impact("AlexSEG", as_of=date(2026, 7, 8)).render())
        print(e.timeline(date(2026, 7, 1), date(2026, 7, 15)).render())
        print(e.brief(as_of=date(2026, 7, 5)).render())
        print(e.ask("How does the AlexSEG maintenance affect SESH?").render())
        print(e.path("C000307:519", "hub:HENRY").render())
        print(e.graph_stats().render())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .store import DEFAULT_DB

# Capability names — the registry of what the facade exposes (used by tests and,
# later, the HTTP/MCP surface to enumerate operations).
CAPABILITIES = ("impact", "timeline", "brief", "ask",
                "path", "neighbors", "graph_stats")


class AsKnownUnsupported(ValueError):
    """Raised when `as_known` is passed to a capability that does not yet
    reconstruct historical knowledge (only `timeline` does). Honest by design:
    the facade will not return a current-knowledge answer under a query that
    asks for what was known at a past instant."""


# ---- response envelope -------------------------------------------------------


@dataclass
class ApiResponse:
    capability: str
    as_of: date | None                 # None for atemporal (graph) capabilities
    as_known: datetime | None = None
    error: str | None = None           # graceful bad-input message; the facade
                                       # never leaks an internal exception to callers
    dataset: dict | None = None        # store snapshot signature (stamped by Engine)

    def render(self) -> str:            # pragma: no cover - overridden
        raise NotImplementedError

    def to_dict(self) -> dict:
        """Structured JSON projection (Era 3). Phase-1 scope: `brief` only — other
        capabilities gain a payload serializer as their screen lands (era3 §4)."""
        raise NotImplementedError(
            f"structured serialization for '{self.capability}' is not yet in scope"
            f" (Era-3 Phase-1 serializes the brief only); add its contract payload"
            f" serializer when its screen lands.")


@dataclass
class ImpactResponse(ApiResponse):
    assessment: object | None = None    # nge.impact.ImpactAssessment | None
    asset: str = ""

    def render(self) -> str:
        if self.assessment is None:
            return (f"No operational event found for '{self.asset}'. Known assets"
                    f" are listed by: SELECT asset_name FROM operational_event.")
        return self.assessment.render()


@dataclass
class TimelineResponse(ApiResponse):
    timeline: object | None = None      # nge.timeline.Timeline

    def render(self) -> str:
        return self.timeline.render()


@dataclass
class BriefResponse(ApiResponse):
    brief: object | None = None         # nge.brief.Brief

    def render(self, explain: bool = False) -> str:
        from .brief import render_markdown
        return render_markdown(self.brief, explain=explain)

    def to_dict(self) -> dict:
        from . import contract
        return contract.envelope("brief", self.as_of, self.as_known, self.dataset,
                                 contract.brief_payload(self.brief))


@dataclass
class AskResponse(ApiResponse):
    answer: object | None = None        # nge.ask.NLAnswer

    def render(self) -> str:
        return self.answer.render()


@dataclass
class PathResponse(ApiResponse):
    origin: str = ""
    destination: str = ""
    paths: list = field(default_factory=list)   # list[nge.graph.PathResult]
    max_hops: int = 4
    min_conf: float = 0.7

    def render(self) -> str:
        if self.error:
            return self.error
        if not self.paths:
            return (f"No paths {self.origin} -> {self.destination} within"
                    f" {self.max_hops} hops at confidence >= {self.min_conf}. Lower"
                    f" the minimum confidence to 0.6 to include leads, or include"
                    f" inactive nodes.")
        out = []
        for i, p in enumerate(self.paths, 1):
            out.append(f"--- option {i} ---")
            out.append(p.explain())
        return "\n".join(out)


@dataclass
class NeighborsResponse(ApiResponse):
    uid: str = ""
    node: object | None = None          # nge.graph.GraphNode
    edges: list = field(default_factory=list)
    _labels: dict = field(default_factory=dict)   # dst uid -> (label, kind, active)

    def render(self) -> str:
        if self.error:
            return self.error
        head = (f"{self.node.label} [{self.node.kind}]"
                + ("" if self.node.active else "  [RETIRED]"))
        out = [head]
        for e in self.edges:
            label, kind, active = self._labels[e.dst]
            lead = "  (lead only)" if "lead" in e.note else ""
            stale = "" if active else "  [RETIRED]"
            out.append(f"  {e.flow_wording():20s} {label} [{kind}]"
                       f"  conf {e.confidence:.2f} via {e.kind}"
                       f" (cite {e.citation}){lead}{stale}")
        return "\n".join(out)


@dataclass
class StatsResponse(ApiResponse):
    stats: dict = field(default_factory=dict)

    def render(self) -> str:
        return "\n".join(f"  {k:24s} {v:5d}" for k, v in self.stats.items())


# ---- the Engine --------------------------------------------------------------


class Engine:
    """The public facade. Owns one DuckDB connection and a cached graph build."""

    def __init__(self, db: str = DEFAULT_DB, con=None, read_only: bool = True):
        if con is not None:
            self._con = con
            self._owns = False
        else:
            import duckdb
            self._con = duckdb.connect(db, read_only=read_only)
            self._owns = True
        self._read_only = read_only
        self._graph_cache = None
        self._dataset_cache = None

    # -- lifecycle --
    def close(self) -> None:
        if self._owns:
            self._con.close()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def con(self):
        return self._con

    def _graph(self):
        if self._graph_cache is None:
            from .graph import build
            self._graph_cache = build(self._con)
        return self._graph_cache

    def dataset(self) -> dict:
        """Cached store snapshot signature (snapshot_id, built_at, boundary),
        stamped on every serialized response so citations/answers are scoped to a
        build (era3 §9 risk #1)."""
        if self._dataset_cache is None:
            from . import contract
            self._dataset_cache = contract.dataset(self._con)
        return self._dataset_cache

    @staticmethod
    def _reject_as_known(cap: str, as_known):
        if as_known is not None:
            raise AsKnownUnsupported(
                f"'{cap}' does not reconstruct historical knowledge yet; only"
                f" 'timeline' honors as_known. Drop as_known, or use timeline for"
                f" the bitemporal view.")

    # -- capability: Operational Impact Engine (§2) --
    def impact(self, asset: str, as_of: date | None = None,
               as_known: datetime | None = None) -> ImpactResponse:
        self._reject_as_known("impact", as_known)
        from .impact import assess
        as_of = as_of or date.today()
        a = assess(self._con, asset, as_of)
        return ImpactResponse(capability="impact", as_of=as_of, assessment=a,
                              asset=asset)

    # -- capability: Operational Timeline (§5) — the one honoring as_known --
    def timeline(self, date_from: date, date_to: date, pipelines=None,
                 assets=None, min_severity: str | None = None,
                 as_of: date | None = None,
                 as_known: datetime | None = None) -> TimelineResponse:
        from .timeline import timeline as _timeline
        t = _timeline(self._con, date_from, date_to, pipelines=pipelines,
                      assets=assets, min_severity=min_severity, as_of=as_of,
                      as_known=as_known)
        return TimelineResponse(capability="timeline", as_of=t.as_of,
                                as_known=t.as_known, timeline=t)

    # -- capability: Morning Brief (§6) --
    def brief(self, as_of: date | None = None, since: datetime | None = None,
              lookahead_days: int = None, generated_at: datetime | None = None,
              narrator=None, record: bool = False,
              as_known: datetime | None = None) -> BriefResponse:
        self._reject_as_known("brief", as_known)
        from .brief import LOOKAHEAD_DAYS, generate
        from .timeline import record_brief_run
        lookahead_days = LOOKAHEAD_DAYS if lookahead_days is None else lookahead_days
        as_of = as_of or date.today()
        b = generate(self._con, as_of=as_of, since=since,
                     lookahead_days=lookahead_days, generated_at=generated_at,
                     narrator=narrator)
        if record:
            if self._read_only:
                raise ValueError("brief(record=True) needs a writable Engine:"
                                 " Engine(read_only=False).")
            record_brief_run(self._con, as_of, as_of,
                             as_of + timedelta(days=lookahead_days))
        return BriefResponse(capability="brief", as_of=b.as_of, brief=b,
                             dataset=self.dataset())

    # -- capability: Natural Language Query (§7) --
    def ask(self, question: str, as_of: date | None = None,
            router: str = "auto",
            as_known: datetime | None = None) -> AskResponse:
        self._reject_as_known("ask", as_known)
        from .ask import ask as _ask
        as_of = as_of or date.today()
        ans = _ask(self._con, question, as_of=as_of, router=router)
        return AskResponse(capability="ask", as_of=as_of, answer=ans)

    # -- capability: Pipeline Relationship Graph (§3), atemporal --
    def path(self, origin: str, destination: str, max_hops: int = 4,
             min_conf: float = 0.7, include_inactive: bool = False,
             limit: int = 10) -> PathResponse:
        g = self._graph()
        for label, uid in (("origin", origin), ("destination", destination)):
            if uid not in g.nodes:
                return PathResponse(capability="path", as_of=None, origin=origin,
                                    destination=destination,
                                    error=f"Unknown {label} node '{uid}'. Use a"
                                          f" point uid (C000307:519), a hub"
                                          f" (hub:HENRY), or a pipeline FERC CID.")
        paths = g.paths(origin, destination, max_hops=max_hops, min_conf=min_conf,
                        active_only=not include_inactive, limit=limit)
        return PathResponse(capability="path", as_of=None, origin=origin,
                            destination=destination, paths=paths,
                            max_hops=max_hops, min_conf=min_conf)

    def neighbors(self, uid: str, min_conf: float = 0.0) -> NeighborsResponse:
        g = self._graph()
        if uid not in g.nodes:
            return NeighborsResponse(capability="neighbors", as_of=None, uid=uid,
                                     error=f"Unknown graph node '{uid}'.")
        node = g.node(uid)
        edges = list(g.neighbors(uid, min_conf=min_conf))
        labels = {e.dst: (g.nodes[e.dst].label, g.nodes[e.dst].kind,
                          g.nodes[e.dst].active) for e in edges}
        return NeighborsResponse(capability="neighbors", as_of=None, uid=uid,
                                 node=node, edges=edges, _labels=labels)

    def graph_stats(self) -> StatsResponse:
        return StatsResponse(capability="graph_stats", as_of=None,
                             stats=self._graph().stats())
