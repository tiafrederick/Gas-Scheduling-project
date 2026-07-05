"""Constraint propagation engine (OI-3.2; DDL-017).

Turns an operational_event into materialized `event_impact` rows: WHO is
exposed, WHY (reason code), HOW BAD (severity after hop decay), HOW SURE
(min-chain confidence), and WHAT TO CHECK (a human investigation) — never a
flow prediction. From public EBB data you can compute exposure and leads; the
scheduler verifies and decides (DDL-005/017).

Propagation walks the Pipeline Relationship Graph's DECLARED interconnect
edges only (each hop keeps its TSP-posting citation) with one direction rule:

  A directional capacity cut (backhaul XOR forwardhaul) shorts what the pipe
  DELIVERS. It propagates across edges whose flow is 'out' or 'both' at the
  constrained pipe's boundary. It does NOT create a counterparty risk across a
  pure-receipt edge (flow 'in'): the counterparty's exposure there is
  injection curtailment, which is below public-data resolution — that point's
  own on-segment row still surfaces it for the desk to check.

Deterministic + idempotent, like derive_events. Rebuilt on every load.
"""
from __future__ import annotations

import hashlib

from .graph import Graph, build
from .severity import compute, decay

# When an event's asset has no segment mapping we can only scope the impact to
# the whole pipeline — real, but locationally vague. Confidence reflects that.
UNMAPPED_SCOPE_CONF = 0.5

# Hop-1 propagation uses resolved point<->point edges only (0.9/1.0 tiers);
# 0.6 CID-only leads inform neighbors(), not materialized impact rows.
MIN_PROPAGATION_CONF = 0.7

MAX_ALTERNATES = 2


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def _event_facts(con, source_notice_uids: list[str]) -> dict:
    """Quantitative context for an event: conservative cut%, services,
    direction, verification state, fact citations."""
    if not source_notice_uids:
        return {"cut_pct": None, "services": [], "direction": None,
                "verified": False, "cites": [], "conf": None}
    ph = ",".join("?" * len(source_notice_uids))
    rows = con.execute(f"""
        SELECT fact_uid, metric, value_low, direction, affects_services,
               verified_by, confidence
        FROM capacity_impact_fact WHERE notice_uid IN ({ph})
    """, source_notice_uids).fetchall()
    design = setting_low = None
    services: list[str] = []
    direction = None
    verified = False
    cites, confs = [], []
    for fuid, metric, lo, dirn, svcs, ver, conf in rows:
        cites.append(f"fact:{fuid}")
        confs.append(conf)
        verified = verified or bool(ver)
        if metric == "design_capacity":
            design = lo
        elif metric == "estimated_capacity_setting":
            setting_low = lo                      # conservative end of the range
            direction = dirn
            services = list(svcs or [])
    cut = (1 - setting_low / design) if (design and setting_low) else None
    return {"cut_pct": cut, "services": services, "direction": direction,
            "verified": verified, "cites": cites,
            "conf": min(confs) if confs else None}


def derive_impacts(con) -> int:
    """Project operational_event × graph -> event_impact. Idempotent rebuild."""
    con.execute("DELETE FROM event_impact")
    g: Graph = build(con)

    events = con.execute("""
        SELECT event_uid, tsp_ferc_cid, event_type, asset_name, seg_cd,
               source_notice_uids, confidence
        FROM operational_event WHERE lifecycle_status != 'superseded'
        ORDER BY event_uid
    """).fetchall()

    seg_conf = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT tsp_ferc_cid, seg_cd, confidence FROM segment_asset_map").fetchall()}

    def insert(event_uid, subject_uid, subject_kind, reason, hop, sev, conf,
               investigation, cites):
        con.execute(
            "INSERT INTO event_impact (impact_uid, event_uid, subject_uid,"
            " subject_kind, reason_code, hop_distance, severity, confidence,"
            " investigation, citations) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [_uid("imp", event_uid, subject_uid, reason), event_uid,
             subject_uid, subject_kind, reason, hop, sev, round(conf, 4),
             investigation, cites])

    n = 0
    for (evt_uid, tsp, etype, asset, seg, notice_uids, evt_conf) in events:
        fx = _event_facts(con, list(notice_uids))
        base_sev, _components = compute(
            event_type=etype, cut_pct=fx["cut_pct"],
            affected_services=fx["services"], seg_mapped=seg is not None,
            facts_verified=fx["verified"])
        base_cites = [f"evt:{evt_uid}"] + fx["cites"]
        chain = [evt_conf] + ([fx["conf"]] if fx["conf"] else [])

        if seg is None:
            # unmapped asset -> pipeline-scoped exposure, honestly vague
            conf = min(min(chain), UNMAPPED_SCOPE_CONF)
            insert(evt_uid, tsp, "pipeline", "on_constrained_pipeline", 0,
                   base_sev, conf,
                   f"'{asset}' has no segment mapping — scope is the whole"
                   f" {g.nodes[tsp].label} system. Check the pipeline's"
                   f" critical notices for the affected stations/points and"
                   f" add a segment_asset_map row once identified.",
                   base_cites + [f"pipeline:{tsp}"])
            n += 1
            continue

        m_conf = seg_conf[(tsp, seg)]
        seed_conf = min(chain + [m_conf])
        seg_cites = base_cites + [f"segmap:{tsp}/{seg}"]
        seg_uid = f"seg:{tsp}:{seg}"
        seeds = sorted(u for u, node in g.nodes.items()
                       if node.kind == "point" and node.attr("segment") == seg_uid)

        affected_pipes: dict[str, float] = {}     # cid -> best conf
        impacted_counterparty_pts: set[str] = set()

        for pt in seeds:
            node = g.nodes[pt]
            flags = "" if node.active else " (RETIRED — verify successor point)"
            insert(evt_uid, pt, "point", "on_constrained_segment", 0, base_sev,
                   seed_conf,
                   f"Point sits on the constrained {seg} segment{flags}."
                   f" Check the OAC screen for {pt} at the next cycle and"
                   f" compare scheduled vs operating capacity.",
                   seg_cites + [f"point:{pt}"])
            n += 1
            if node.attr("loc_type") == "STR":
                insert(evt_uid, pt, "point", "storage_service_at_risk", 0,
                       base_sev, seed_conf,
                       f"Storage interconnect {pt} ({node.label}) sits on the"
                       f" constrained segment — verify injection/withdrawal"
                       f" nominations will still flow.",
                       seg_cites + [f"point:{pt}"])
                n += 1

            # hop 1: declared interconnect edges off this seed point
            for e in g.edges:
                if e.src != pt or e.kind != "interconnect":
                    continue
                if e.confidence < MIN_PROPAGATION_CONF:
                    continue
                if g.nodes[e.dst].kind != "point":
                    continue
                if fx["direction"] in ("backhaul", "forwardhaul") and e.flow == "in":
                    continue    # the direction rule (module docstring)
                dst = e.dst
                dst_pipe = g.nodes[dst].attr("pipeline")
                hop_conf = min(seed_conf, e.confidence)
                hop_sev = decay(base_sev, 1)
                insert(evt_uid, dst, "point", "downstream_interconnect", 1,
                       hop_sev, hop_conf,
                       f"{g.nodes[dst_pipe].label} interconnect {dst} takes"
                       f" service from constrained point {pt}"
                       f" ({e.flow_wording()} relationship). Confirm receipts"
                       f" are not cut at Timely; watch intraday revisions.",
                       seg_cites + [f"point:{pt}", f"ic:{e.citation}",
                                    f"point:{dst}"])
                n += 1
                impacted_counterparty_pts.add(dst)
                if affected_pipes.get(dst_pipe, 0) < hop_conf:
                    affected_pipes[dst_pipe] = hop_conf

        # contract exposure + alternates on affected PORTFOLIO pipes
        for pipe_cid in sorted(affected_pipes):
            if not g.nodes[pipe_cid].attr("is_portfolio", False):
                continue
            alternates = _alternates(g, pipe_cid, tsp, impacted_counterparty_pts)
            holdings = con.execute("""
                SELECT holding_uid, contract_id, mdq_dth FROM contract_holding
                WHERE tsp_ferc_cid = ? AND holder_name ILIKE 'BP %'
                ORDER BY contract_id
            """, [pipe_cid]).fetchall()
            for h_uid, k, mdq in holdings:
                alt_txt = ("; alternates to verify: " + "; ".join(a[0] for a in alternates)
                           ) if alternates else ""
                alt_cites = [c for a in alternates for c in a[1]]
                insert(evt_uid, h_uid, "holding", "contract_at_affected_point",
                       1, decay(base_sev, 1),
                       min(affected_pipes[pipe_cid], 1.0),
                       f"BP contract {k} ({mdq:,} Dth/d) is on affected"
                       f" {g.nodes[pipe_cid].label} — review nominations at its"
                       f" receipt/delivery points{alt_txt}.",
                       seg_cites + [f"holding:{h_uid}"] + alt_cites)
                n += 1
    return n


def _alternates(g: Graph, affected_pipe: str, constrained_pipe: str,
                impacted: set[str]) -> list[tuple[str, list[str]]]:
    """Verification leads for re-sourcing, cited. Two families a scheduler
    actually reaches for: (a) the affected pipe's OTHER receipt interconnects
    (not implicated in this event), (b) storage adjacent to the constrained
    pipe via full-confidence round-trips."""
    out: list[tuple[str, list[str]]] = []

    receipts = []
    for uid, node in g.nodes.items():
        if (node.kind == "point" and node.attr("pipeline") == affected_pipe
                and node.active and uid not in impacted
                and (node.attr("dir_flo") or "").upper() == "R"):
            best = max((e.confidence for e in g.neighbors(uid)
                        if e.kind == "interconnect"), default=0.0)
            if best > 0:
                receipts.append((-best, uid, best))
    for _negconf, uid, best in sorted(receipts)[:MAX_ALTERNATES]:
        label = "lead 0.6 — counterparty catalog not ingested" if best < 0.7 \
                else f"conf {best}"
        out.append((f"alternate receipt {uid} ({label})", [f"point:{uid}"]))

    for e in g.edges:
        if (e.kind == "interconnect" and e.confidence == 1.0
                and g.nodes[e.src].kind == "point"
                and g.nodes[e.src].attr("pipeline") == constrained_pipe
                and g.nodes[e.dst].attr("pipeline") in
                {u for u, nd in g.nodes.items() if nd.kind == "storage_facility"}):
            stor = g.nodes[e.dst].attr("pipeline")
            out.append((f"storage adjacent to constrained pipe:"
                        f" {g.nodes[stor].label} via {e.src} <-> {e.dst}"
                        f" (roundtrip 1.0)",
                        [f"ic:{e.citation}", f"point:{e.dst}"]))
    return out
