"""v1 impact analysis: constrained asset -> cross-pipeline exposure, fully cited.

Answers the Phase-2 vertical-slice question (DDL-008):
  "How does the CGT AlexSEG maintenance affect SESH and BP's delivery points?"

Every hop in the answer cites the store row it came from (fact_uid, notice_uid,
interconnect_uid, point_uid, holding_uid) — the eval rule is NO UN-CITED HOP.
Reachability is a recursive walk over resolved interconnect edges (the derived
graph read-model, DDL-001/010); no graph database involved.

Run:  PYTHONPATH=src python3 -m nge.reach --asset AlexSEG
"""
from __future__ import annotations

import argparse

import duckdb

from .store import DEFAULT_DB

# Only walk edges at/above this resolution confidence (see canonical-model.md tiers).
MIN_EDGE_CONFIDENCE = 0.9


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
    w("NOTE: asset→point mapping for CGT segments is pending CGT location data;")
    w("impact below is shown at pipeline-interconnect granularity (known gap).")
    w("")

    # 2) Recursive reachability from the constrained pipeline over resolved edges.
    reach = con.execute("""
        WITH RECURSIVE hops AS (
            SELECT i.interconnect_uid, i.a_tsp_ferc_cid AS from_cid,
                   i.b_tsp_ferc_cid AS to_cid, i.a_point_uid, i.b_point_uid,
                   i.resolution_status, i.resolution_confidence, 1 AS depth,
                   i.a_point_uid || ' -> ' || coalesce(i.b_point_uid,'?') AS path
            FROM interconnect i
            WHERE i.a_tsp_ferc_cid = ? AND i.b_tsp_ferc_cid IS NOT NULL
              AND i.resolution_confidence >= ?
            UNION ALL
            SELECT i.interconnect_uid, i.a_tsp_ferc_cid, i.b_tsp_ferc_cid,
                   i.a_point_uid, i.b_point_uid,
                   i.resolution_status, i.resolution_confidence, h.depth + 1,
                   h.path || ' -> ' || coalesce(i.b_point_uid,'?')
            FROM interconnect i
            JOIN hops h ON i.a_tsp_ferc_cid = h.to_cid
            WHERE i.b_tsp_ferc_cid IS NOT NULL
              AND i.b_tsp_ferc_cid <> h.from_cid          -- no immediate backtrack
              AND i.resolution_confidence >= ?
              AND h.depth < 3
        )
        SELECT DISTINCT h.interconnect_uid, h.from_cid, h.to_cid, h.a_point_uid,
               h.b_point_uid, h.resolution_status, h.resolution_confidence,
               h.depth, h.path, p.name, p.is_portfolio
        FROM hops h JOIN pipeline p ON p.ferc_cid = h.to_cid
        ORDER BY h.depth, h.to_cid
    """, [pipe_cid, MIN_EDGE_CONFIDENCE, MIN_EDGE_CONFIDENCE]).fetchall()

    w(f"Downstream/upstream pipelines reachable from {pipe_code} over edges with"
      f" confidence >= {MIN_EDGE_CONFIDENCE}:")
    if not reach:
        w("  (none at this confidence — ingest more counterparty point catalogs)")
    affected_cids: list[str] = []
    for (ic_uid, _f, to_cid, a_pt, b_pt, status, conf, depth, path, name,
         portfolio) in reach:
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
