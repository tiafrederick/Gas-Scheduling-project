"""Pipeline Relationship Graph — the foundational model the reasoning engine
operates on (DDL-015; design: docs/operational-intelligence.md §3).

A typed, in-memory PROJECTION of the canonical store — never a store itself
(DDL-001/010). Rebuilt on demand from `pipeline`, `point`, `interconnect`,
`segment_asset_map`-adjacent segment codes, and the curated `market_hub` /
`hub_member` seeds. The graph invents no identity: every node/edge uid is a
canonical key, and every edge carries the citation of the row it came from.

Scheduler semantics encoded here on purpose:
  * Interconnect edges carry FLOW direction derived from the a-side point's
    Dir Flo code (R = gas flows INTO the a-side pipe, D = out of it, B = both).
    A scheduler never sees an interconnect as undirected — "SESH receives from
    CGT at Delhi" — even though *pathfinding* stays direction-agnostic
    (commercial paths are negotiable; physical flow is not).
  * active_only=True by default on traversals: you don't nominate to a retired
    meter. Retired points remain in the graph, flagged, and can be included on
    request — that is how the SESH->4208 staleness stays visible.
  * "Lead" edges: a point that declares only its counterparty PIPELINE (no
    resolvable point) still gets an edge to that pipeline node at the tier's
    confidence. A desk knows "this meter talks to Transco" long before it has
    Transco's point catalog. Default min_confidence keeps leads out of
    recommended paths; neighbors() shows them, labeled.

Confidence algebra: path confidence = min(edge confidences) — weakest link,
matching how a scheduler trusts a path exactly as much as its sketchiest hop
(OI doc §1.3). It never increases along a chain (property-tested).

Stdlib only. No networkx. Deterministic: two builds over the same store yield
identical structures and identical traversal orderings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# -- vocabularies -------------------------------------------------------------

NODE_KINDS = ("pipeline", "storage_facility", "point", "segment", "market_hub")
EDGE_KINDS = ("interconnect", "on_segment", "of_pipeline", "hub_member")

# FERC CIDs of entities whose primary business is storage. Curated registry
# (same spirit as resolve.KNOWN_PIPELINES): a scheduler asking "what storage can
# I reach?" wants these typed as storage, not as generic pipelines.
STORAGE_OPERATOR_CIDS = {
    "C000086": "Egan Hub Storage, LLC",
    "C001706": "Bobcat Gas Storage",
    "C001058": "Pine Prairie Energy Center, LLC",
    "C001773": "Jefferson Island & Storage Hub L.L.C.",
    "C003409": "Perryville Gas Storage LLC",
    "C001199": "Mississippi Hub, LLC",
    "C001593": "SG Resources Mississippi, L.L.C. (Petal)",
}

# Dir Flo -> flow wording, from the a-side point's perspective.
# R: the a-side pipe RECEIVES gas here (flow in). D: DELIVERS (flow out).
# B: bidirectional. Anything else/unknown -> None (no flow claim made).
_FLOW_BY_DIR = {"R": "in", "D": "out", "B": "both"}
_FLOW_WORDING = {
    "in": "receives from",
    "out": "delivers to",
    "both": "bidirectional with",
    None: "connects to",
}


# -- typed structure ----------------------------------------------------------

@dataclass(frozen=True)
class GraphNode:
    uid: str                       # canonical key: 'C000307:519', 'seg:C000307:ALEXDRIA', 'hub:HENRY'
    kind: str                      # one of NODE_KINDS
    label: str
    attrs: tuple = ()              # sorted (key, value) pairs — hashable + deterministic

    def attr(self, key: str, default=None):
        for k, v in self.attrs:
            if k == key:
                return v
        return default

    @property
    def active(self) -> bool:
        return self.attr("active", True)


@dataclass(frozen=True)
class GraphEdge:
    src: str
    dst: str
    kind: str                      # one of EDGE_KINDS
    confidence: float
    citation: str                  # interconnect_uid / seed-row ref / catalog source
    flow: Optional[str] = None     # 'in'|'out'|'both' from src's perspective (interconnect only)
    note: str = ""

    def flow_wording(self) -> str:
        return _FLOW_WORDING.get(self.flow, _FLOW_WORDING[None])


@dataclass
class PathResult:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @property
    def hops(self) -> int:
        return len(self.edges)

    @property
    def confidence(self) -> float:
        """min() composition — the weakest link governs (DDL-015)."""
        return min((e.confidence for e in self.edges), default=1.0)

    def explain(self) -> str:
        """Hop-by-hop narrative with flow wording, confidence, and citations."""
        if not self.edges:
            return f"{self.nodes[0].label}: start == destination (0 hops)."
        lines = [f"Path: {self.hops} hop(s), confidence {self.confidence:.2f} "
                 f"(min of per-hop confidences)"]
        for i, e in enumerate(self.edges):
            a, b = self.nodes[i], self.nodes[i + 1]
            stale_a = "" if a.active else " [RETIRED]"
            stale_b = "" if b.active else " [RETIRED]"
            lines.append(
                f"  {i+1}. {a.label}{stale_a} {e.flow_wording()} {b.label}{stale_b}"
                f"  [{e.kind}, conf {e.confidence:.2f}, cite {e.citation}]")
        return "\n".join(lines)


# -- the graph ----------------------------------------------------------------

class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self._adj: dict[str, list[GraphEdge]] = {}
        self.edges: list[GraphEdge] = []

    # construction ------------------------------------------------------------
    def _add_node(self, node: GraphNode) -> None:
        self.nodes.setdefault(node.uid, node)

    def _add_edge(self, edge: GraphEdge, mirror: bool = True) -> None:
        """Store the edge and (by default) an implicit reverse for traversal.
        The reverse flips flow perspective ('in' <-> 'out')."""
        self.edges.append(edge)
        self._adj.setdefault(edge.src, []).append(edge)
        if mirror:
            rev_flow = {"in": "out", "out": "in", "both": "both"}.get(edge.flow)
            self._adj.setdefault(edge.dst, []).append(GraphEdge(
                src=edge.dst, dst=edge.src, kind=edge.kind,
                confidence=edge.confidence, citation=edge.citation,
                flow=rev_flow, note=edge.note))

    def _finalize(self) -> None:
        """Deterministic adjacency ordering: confidence desc, kind, dst uid."""
        for uid in self._adj:
            self._adj[uid].sort(key=lambda e: (-e.confidence, e.kind, e.dst))

    # queries -----------------------------------------------------------------
    def node(self, uid: str) -> GraphNode:
        if uid not in self.nodes:
            raise KeyError(f"unknown graph node: {uid!r}")
        return self.nodes[uid]

    def neighbors(self, uid: str, kinds: Optional[Iterable[str]] = None,
                  min_conf: float = 0.0) -> list[GraphEdge]:
        self.node(uid)  # loud on unknown uid
        kindset = set(kinds) if kinds else None
        return [e for e in self._adj.get(uid, [])
                if e.confidence >= min_conf
                and (kindset is None or self.nodes[e.dst].kind in kindset)]

    def paths(self, a: str, b: str, max_hops: int = 4, min_conf: float = 0.7,
              active_only: bool = True, limit: int = 20) -> list[PathResult]:
        """All simple paths a->b up to max_hops, deterministic order:
        (hops asc, confidence desc, path-uid tiebreak). Bounded + capped.

        min_conf defaults to 0.7 — just ABOVE the 0.6 'lead' tier: leads inform
        (visible in neighbors()), they don't route. Lower it explicitly to
        explore lead-quality paths."""
        self.node(a); self.node(b)
        results: list[PathResult] = []
        # iterative DFS with explicit stack keeps recursion bounded + orderable
        stack: list[tuple[str, list[GraphEdge]]] = [(a, [])]
        while stack:
            here, trail = stack.pop()
            if len(trail) >= max_hops:
                continue
            # reversed() so the highest-confidence neighbor is explored first
            for e in reversed(self._adj.get(here, [])):
                if e.confidence < min_conf:
                    continue
                dst_node = self.nodes[e.dst]
                if active_only and not dst_node.active and e.dst != b:
                    continue
                if any(t.src == e.dst or t.dst == e.dst for t in trail) or e.dst == a:
                    continue  # simple paths only
                new_trail = trail + [e]
                if e.dst == b:
                    node_seq = [self.nodes[a]] + [self.nodes[t.dst] for t in new_trail]
                    results.append(PathResult(nodes=node_seq, edges=new_trail))
                else:
                    stack.append((e.dst, new_trail))
        results.sort(key=lambda p: (p.hops, -p.confidence,
                                    tuple(n.uid for n in p.nodes)))
        return results[:limit]

    def subgraph(self, scope_uid: str) -> "Graph":
        """Nodes belonging to a pipeline / segment / hub, plus edges among them
        and their one-hop interconnect fringe."""
        scope = self.node(scope_uid)
        member_uids: set[str] = {scope_uid}
        if scope.kind in ("pipeline", "storage_facility"):
            member_uids |= {u for u, n in self.nodes.items()
                            if n.kind == "point" and n.attr("pipeline") == scope_uid}
        elif scope.kind == "segment":
            member_uids |= {u for u, n in self.nodes.items()
                            if n.kind == "point" and n.attr("segment") == scope_uid}
        elif scope.kind == "market_hub":
            member_uids |= {e.dst for e in self._adj.get(scope_uid, [])
                            if e.kind == "hub_member"}
        sub = Graph()
        for uid in member_uids:
            sub._add_node(self.nodes[uid])
        for e in self.edges:
            if e.src in member_uids or e.dst in member_uids:
                for uid in (e.src, e.dst):
                    sub._add_node(self.nodes[uid])
                sub._add_edge(e)
        sub._finalize()
        return sub

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes.values():
            out[f"nodes.{n.kind}"] = out.get(f"nodes.{n.kind}", 0) + 1
        for e in self.edges:
            out[f"edges.{e.kind}"] = out.get(f"edges.{e.kind}", 0) + 1
        return dict(sorted(out.items()))

    # exports (future UI wire format) ------------------------------------------
    def to_json(self) -> dict:
        return {
            "nodes": [{"uid": n.uid, "kind": n.kind, "label": n.label,
                       "attrs": dict(n.attrs)}
                      for n in sorted(self.nodes.values(), key=lambda n: n.uid)],
            "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind,
                       "confidence": e.confidence, "flow": e.flow,
                       "citation": e.citation}
                      for e in sorted(self.edges,
                                      key=lambda e: (e.src, e.dst, e.kind))],
        }

    def to_dot(self) -> str:
        shape = {"pipeline": "box", "storage_facility": "cylinder",
                 "point": "ellipse", "segment": "hexagon", "market_hub": "star"}
        lines = ["graph nge {", "  rankdir=LR;"]
        for n in sorted(self.nodes.values(), key=lambda n: n.uid):
            style = ', style=dashed' if not n.active else ""
            lines.append(f'  "{n.uid}" [label="{n.label}", '
                         f'shape={shape[n.kind]}{style}];')
        for e in sorted(self.edges, key=lambda e: (e.src, e.dst, e.kind)):
            lines.append(f'  "{e.src}" -- "{e.dst}" '
                         f'[label="{e.kind} {e.confidence:.2f}"];')
        lines.append("}")
        return "\n".join(lines)


# -- builder ------------------------------------------------------------------

def build(con) -> Graph:
    """Project the canonical store into a Graph. Deterministic; <1s at portfolio
    scale. `con` is a DuckDB connection to a loaded store."""
    g = Graph()

    # pipeline / storage_facility nodes
    for cid, name, short_code, is_portfolio in con.execute(
            "SELECT ferc_cid, name, short_code, coalesce(is_portfolio, FALSE)"
            " FROM pipeline ORDER BY ferc_cid").fetchall():
        kind = "storage_facility" if cid in STORAGE_OPERATOR_CIDS else "pipeline"
        g._add_node(GraphNode(
            uid=cid, kind=kind, label=short_code or name,
            attrs=(("is_portfolio", bool(is_portfolio)), ("name", name),
                   ("short_code", short_code))))

    # segment nodes (only CGT's catalog carries segment codes today)
    for cid, seg in con.execute(
            "SELECT DISTINCT tsp_ferc_cid, pipeline_seg_cd FROM point"
            " WHERE pipeline_seg_cd IS NOT NULL ORDER BY 1, 2").fetchall():
        g._add_node(GraphNode(
            uid=f"seg:{cid}:{seg}", kind="segment", label=f"{seg} ({cid})",
            attrs=(("pipeline", cid), ("seg_cd", seg))))

    # point nodes + of_pipeline + on_segment edges
    for (uid, cid, name, stat, dir_flo, seg, zone, cnty, ltype, src) in con.execute(
            "SELECT point_uid, tsp_ferc_cid, loc_name, loc_stat_ind, dir_flo,"
            " pipeline_seg_cd, loc_zone, loc_cnty, loc_type_ind, source_file"
            " FROM point ORDER BY point_uid").fetchall():
        active = (stat or "A").upper() != "I"
        seg_uid = f"seg:{cid}:{seg}" if seg else None
        g._add_node(GraphNode(
            uid=uid, kind="point", label=f"{uid} {name or ''}".strip(),
            attrs=tuple(sorted({
                "active": active, "pipeline": cid, "segment": seg_uid,
                "dir_flo": dir_flo, "zone": zone, "county": cnty,
                "loc_type": ltype, "is_storage_point": (ltype or "") == "STR",
            }.items()))))
        g._add_edge(GraphEdge(src=uid, dst=cid, kind="of_pipeline",
                              confidence=1.0, citation=src or "point catalog"),
                    mirror=True)
        if seg_uid:
            g._add_edge(GraphEdge(src=uid, dst=seg_uid, kind="on_segment",
                                  confidence=1.0, citation=src or "point catalog"),
                        mirror=True)

    # interconnect edges: point->point when resolved; point->pipeline "lead"
    # when only the counterparty pipe is known (conf >= 0.4). A physical
    # interconnect flows both ways commercially, so a ONE-sided declaration is
    # mirrored for traversal — but where the counterparty posted its own row
    # (roundtrips), that row IS the reverse direction: no synthetic mirror,
    # each direction keeps its own citation.
    ic_rows = con.execute(
        "SELECT interconnect_uid, a_point_uid, b_point_uid, b_tsp_ferc_cid,"
        " resolution_confidence, resolution_status, dir_flo"
        " FROM interconnect ORDER BY interconnect_uid").fetchall()
    declared_pairs = {(r[1], r[2]) for r in ic_rows if r[2]}
    for (ic_uid, a_uid, b_uid, b_cid, conf, status, dir_flo) in ic_rows:
        if a_uid not in g.nodes:
            continue
        flow = _FLOW_BY_DIR.get((dir_flo or "").upper())
        if b_uid and b_uid in g.nodes:
            reciprocal = (b_uid, a_uid) in declared_pairs
            g._add_edge(GraphEdge(src=a_uid, dst=b_uid, kind="interconnect",
                                  confidence=conf, citation=ic_uid, flow=flow,
                                  note=status), mirror=not reciprocal)
        elif b_cid and b_cid in g.nodes and conf >= 0.4:
            g._add_edge(GraphEdge(src=a_uid, dst=b_cid, kind="interconnect",
                                  confidence=conf, citation=ic_uid, flow=flow,
                                  note=f"{status} (lead: counterparty point not"
                                       f" yet cataloged)"), mirror=True)

    # market hubs + membership edges
    for hub_id, name, region in con.execute(
            "SELECT hub_id, name, region FROM market_hub ORDER BY hub_id").fetchall():
        g._add_node(GraphNode(uid=f"hub:{hub_id}", kind="market_hub", label=name,
                              attrs=(("region", region),)))
    for hub_id, point_uid, conf, note in con.execute(
            "SELECT hub_id, point_uid, confidence, note FROM hub_member"
            " ORDER BY hub_id, point_uid").fetchall():
        g._add_edge(GraphEdge(src=f"hub:{hub_id}", dst=point_uid,
                              kind="hub_member", confidence=conf,
                              citation=f"hub_member seed ({hub_id})", note=note),
                    mirror=True)

    g._finalize()
    return g
