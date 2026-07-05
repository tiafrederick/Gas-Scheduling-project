"""v1 impact analysis: constrained asset -> cross-pipeline exposure, fully cited.

Answers the Phase-2 vertical-slice question (DDL-008):
  "How does the CGT AlexSEG maintenance affect SESH and BP's delivery points?"

Every hop in the answer cites the store row it came from (fact_uid, notice_uid,
interconnect_uid, point_uid, holding_uid) — the eval rule is NO UN-CITED HOP.
Reachability runs over the Pipeline Relationship Graph (nge/graph.py, DDL-015)
— the foundational model of the reasoning layer — walking only DECLARED
interconnect edges (each hop keeps the citation of the TSP posting it came
from; synthetic traversal mirrors are never used here).

Run:  PYTHONPATH=src python3 -m nge.reach --asset AlexSEG
"""
from __future__ import annotations

import argparse

import duckdb

from .graph import Graph, build
from .store import DEFAULT_DB

# Only walk edges at/above this resolution confidence (see canonical-model.md tiers).
MIN_EDGE_CONFIDENCE = 0.9
MAX_DEPTH = 3


def _cross_pipe_hops(g: Graph, seed_points: list[str] | None,
                     seed_pipe: str) -> list[tuple]:
    """Expand cross-pipeline reachability over DECLARED interconnect edges.

    Faithful port of the original recursive-CTE semantics: hop 1 starts from
    the seed points (or from every point of the seed pipe when no segment
    mapping exists); later hops continue PIPE-level — from any declared edge of
    a reached pipe — never hopping straight back to the pipe just left. Only
    g.edges (originals) are walked, never synthetic traversal mirrors, so every
    hop cites a real TSP posting.

    Returns rows (ic_uid, from_cid, to_cid, a_uid, b_uid, status, conf, depth,
    path), deduped, deterministically ordered by (depth, to_cid, path).
    """
    edges = [e for e in g.edges
             if e.kind == "interconnect"
             and e.confidence >= MIN_EDGE_CONFIDENCE
             and g.nodes[e.dst].kind == "point"]
    by_pipe: dict[str, list] = {}
    for e in edges:
        by_pipe.setdefault(g.nodes[e.src].attr("pipeline"), []).append(e)

    rows: set[tuple] = set()
    frontier: list[tuple[str, str, str]] = []   # (from_cid, to_cid, path)

    base = ([e for e in edges if e.src in set(seed_points)]
            if seed_points is not None else by_pipe.get(seed_pipe, []))
    for e in base:
        from_cid = g.nodes[e.src].attr("pipeline")
        to_cid = g.nodes[e.dst].attr("pipeline")
        path = f"{e.src} -> {e.dst}"
        rows.add((e.citation, from_cid, to_cid, e.src, e.dst, e.note,
                  e.confidence, 1, path))
        frontier.append((from_cid, to_cid, path))

    depth = 2
    while frontier and depth <= MAX_DEPTH:
        nxt: list[tuple[str, str, str]] = []
        for from_cid, to_cid, path in frontier:
            for e in by_pipe.get(to_cid, []):
                b_pipe = g.nodes[e.dst].attr("pipeline")
                if b_pipe == from_cid:      # no immediate backtrack
                    continue
                new_path = f"{path} -> {e.dst}"
                row = (e.citation, to_cid, b_pipe, e.src, e.dst, e.note,
                       e.confidence, depth, new_path)
                if row not in rows:
                    rows.add(row)
                    nxt.append((to_cid, b_pipe, new_path))
        frontier = nxt
        depth += 1

    return sorted(rows, key=lambda r: (r[7], r[2], r[8]))


def impact_report(con: duckdb.DuckDBPyConnection, asset: str) -> str:
    out: list[str] = []
    w = out.append

    # 1) The constraint facts for the asset (with provenance)
    facts = con.execute("""
        SELECT f.fact_uid, f.notice_uid, f.metric, f.value_low, f.value_high, f.uom,
               f.direction, f.valid_gas_day_from, f.valid_gas_day_to,
               f.effective_cycle, f.confidence, f.verified_by, f.tsp_ferc_cid,
               n.subject, n.notice_type, n.critical, p.name AS pipe_name, p.short_code
        FROM capacity_impact_fact f
        JOIN notice n USING (notice_uid)
        JOIN pipeline p ON p.ferc_cid = f.tsp_ferc_cid
        WHERE lower(f.asset_name) = lower(?)
        ORDER BY f.metric
    """, [asset]).fetchall()
    if not facts:
        return f"No capacity impact facts found for asset '{asset}'."

    pipe_cid, pipe_name, pipe_code = facts[0][12], facts[0][16], facts[0][17]
    w(f"IMPACT ANALYSIS — asset '{asset}' on {pipe_name} [{pipe_code}]")
    w("=" * 74)
    w(f"Notice: {facts[0][13]}")
    w(f"        [{facts[0][1]}] type={facts[0][14]} critical={facts[0][15]}")
    w("")
    w("Constraint facts (extracted, provenance-tracked):")
    for (fact_uid, _n, metric, lo, hi, uom, direction, gd_from, gd_to, cycle,
         conf, verified, *_rest) in facts:
        rng = f"{lo:,.0f}" + (f"–{hi:,.0f}" if hi != lo else "")
        win = f" gas days {gd_from}→{gd_to}" if gd_from else ""
        cyc = f" eff {cycle}" if cycle else ""
        ver = "verified" if verified else "UNVERIFIED"
        w(f"  • {metric}: {rng} {uom} [{direction or 'n/a'}]{win}{cyc}"
          f"  (fact {fact_uid}, conf {conf}, {ver})")
    w("")

    # 2) asset -> segment(s) -> points (DDL-013). Every mapping row is confidence-
    # tagged and cited; assets with no mapping fall back to pipeline-level reach.
    seg_rows = con.execute("""
        SELECT seg_cd, confidence, note FROM segment_asset_map
        WHERE tsp_ferc_cid = ? AND lower(asset_name) = lower(?)
    """, [pipe_cid, asset]).fetchall()

    start_points: list[tuple[str, str]] = []  # (point_uid, seg_cd)
    if seg_rows:
        w(f"Asset -> segment mapping (segment_asset_map, DDL-013):")
        for seg_cd, mconf, note in seg_rows:
            w(f"  • {asset} -> segment '{seg_cd}'  (confidence {mconf})")
            w(f"      {note}")
        w("")
        placeholders = ",".join("?" * len(seg_rows))
        pts = con.execute(f"""
            SELECT point_uid, pipeline_seg_cd FROM point
            WHERE tsp_ferc_cid = ? AND pipeline_seg_cd IN ({placeholders})
        """, [pipe_cid, *[s[0] for s in seg_rows]]).fetchall()
        start_points = pts
        w(f"Points on the mapped segment(s): {len(start_points)}"
          f" ({', '.join(p for p, _ in start_points)})")
        w("")
    else:
        w(f"NOTE: no segment_asset_map entry for asset '{asset}' — falling back to"
          f" pipeline-level reachability (coarser, but nothing is hidden).")
        w("")

    # 3) Reachability over the Pipeline Relationship Graph (DDL-015), seeded at
    # the asset's own points when known, else the whole constrained pipeline
    # (graceful degradation). Declared edges only — every hop stays cited.
    g = build(con)
    seed_uids = [p for p, _ in start_points] if start_points else None
    reach = _cross_pipe_hops(g, seed_uids, pipe_cid)

    w(f"Downstream/upstream pipelines reachable from {pipe_code} over edges with"
      f" confidence >= {MIN_EDGE_CONFIDENCE}:")
    if not reach:
        w("  (none at this confidence — ingest more counterparty point catalogs)")
    affected_cids: list[str] = []
    for (ic_uid, _from, to_cid, a_pt, b_pt, status, conf, depth, path) in reach:
        pipe_node = g.nodes[to_cid]
        portfolio = pipe_node.attr("is_portfolio", False)
        name = pipe_node.attr("name", to_cid)
        flag = "PORTFOLIO PIPE" if portfolio else "external"
        w(f"  hop {depth}: {path}")
        w(f"         -> {name} [{to_cid}] ({flag})")
        w(f"            via interconnect {ic_uid} [{status}, conf {conf}]")
        if portfolio and to_cid not in affected_cids:
            affected_cids.append(to_cid)
    w("")

    # 3) BP exposure on affected portfolio pipelines.
    for cid in affected_cids:
        rows = con.execute("""
            SELECT h.holding_uid, h.contract_id, h.rate_schedule, h.mdq_dth,
                   h.term_start, h.term_end, cp.loc, cp.loc_name, cp.point_uid
            FROM contract_holding h
            LEFT JOIN contract_point cp USING (holding_uid)
            WHERE h.tsp_ferc_cid = ? AND h.holder_name ILIKE 'BP %'
            ORDER BY h.contract_id, cp.loc
        """, [cid]).fetchall()
        pname = con.execute("SELECT name FROM pipeline WHERE ferc_cid=?",
                            [cid]).fetchone()[0]
        w(f"BP ENERGY exposure on {pname} [{cid}]:")
        if not rows:
            w("  (no BP holdings loaded for this pipeline)")
            continue
        seen = set()
        for (h_uid, k, sched, mdq, t0, t1, loc, loc_name, pt_uid) in rows:
            if h_uid not in seen:
                seen.add(h_uid)
                w(f"  • Contract {k} ({sched}) MDQ {mdq:,} Dth/d,"
                  f" term {t0}→{t1}  (holding {h_uid})")
            if loc:
                cited = pt_uid or "point not in catalog"
                w(f"      - point {loc} {loc_name}  [{cited}]")
    w("")
    w("Every hop above cites a store row (fact/notice/interconnect/point/holding).")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cited cross-pipeline impact analysis")
    ap.add_argument("--asset", default="AlexSEG")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    try:
        print(impact_report(con, args.asset))
    finally:
        con.close()


if __name__ == "__main__":
    main()
