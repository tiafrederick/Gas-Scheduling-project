"""Intent registry (OI-6.2; DDL-019, OI doc §7.3-7.4).

A whitelisted, typed registry of query intents, each backed by a deterministic,
cited executor that is mostly a thin wrapper over the OI-2..OI-5 APIs. This is
the alternative to text-to-SQL, which DDL-019 rejects as unauditable and
injection-prone: the ONLY questions the engine answers are the ones an executor
here can answer, and every executor uses parameterized queries — a malformed
param can never reach the SQL as text.

Two intents (`capacity_at_point`, `storage_status`) ship as HONEST STUBS: they
are registered and executable, but the data (OAC screens, storage balances)
isn't loaded yet, so they answer with what the engine *can* say plus the exact
EBB check to run. The registry is complete even where the data isn't.

This module imports with no duckdb and no API key — the router (nge.ask) reads
`REGISTRY` metadata (names, examples, param schemas) without a database; the
heavy imports (graph/impact/timeline) are lazy, inside the executors.

`REGISTRY` is the future UI/MCP query surface, not just an internal detail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

_POINT_UID_RX = re.compile(r"^C\d{6}:[A-Za-z0-9]+$")
_ISO_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ExecResult:
    text: str                       # the rendered answer
    citations: list[str] = field(default_factory=list)
    answer_confidence: float = 1.0  # executor's confidence in the ANSWER (§7.6),
                                    # kept separate from routing confidence
    ok: bool = True                 # False = graceful "can't answer this" (not a crash)


@dataclass
class Intent:
    name: str
    description: str
    params_schema: dict             # JSON-schema `properties` for the LLM tool
    required: list[str]
    examples: list[str]             # gold questions — also user-facing docs (§7.8)
    executor: Callable              # (con, params: dict, as_of: date) -> ExecResult


# ---- shared helpers ----------------------------------------------------------


def _as_of(params: dict, as_of: date) -> date:
    v = params.get("as_of")
    if isinstance(v, str) and _ISO_DATE_RX.match(v):
        return date.fromisoformat(v)
    return as_of


def _resolve_node(con, token: str) -> str | None:
    """Desk-token -> graph node uid: a point uid, a market-hub name, or a
    pipeline short-code. Returns None if it resolves to nothing (never guesses)."""
    if not token:
        return None
    t = token.strip()
    if _POINT_UID_RX.match(t):
        row = con.execute("SELECT 1 FROM point WHERE point_uid = ?", [t]).fetchone()
        return t if row else None
    hub = con.execute("SELECT hub_id FROM market_hub WHERE upper(hub_id) = upper(?)"
                      " OR upper(name) = upper(?)", [t, t]).fetchone()
    if hub:
        return f"hub:{hub[0]}"
    pipe = con.execute("SELECT ferc_cid FROM pipeline WHERE upper(short_code) ="
                       " upper(?) OR upper(ferc_cid) = upper(?)", [t, t]).fetchone()
    if pipe:
        return pipe[0]
    return None


# ---- executors ---------------------------------------------------------------


def _exec_asset_impact(con, params, as_of) -> ExecResult:
    from .impact import assess
    asset = str(params.get("asset") or "").strip()
    if not asset:
        return ExecResult("Which asset? e.g. 'AlexSEG' or 'Corinth Compressor"
                          " Station'.", ok=False, answer_confidence=0.0)
    a = assess(con, asset, _as_of(params, as_of))
    if a is None:
        known = [r[0] for r in con.execute(
            "SELECT DISTINCT asset_name FROM operational_event ORDER BY 1").fetchall()]
        return ExecResult(f"No operational event matches '{asset}'. Known assets:"
                          f" {', '.join(known)}.", ok=False, answer_confidence=0.0)
    return ExecResult(a.render(), citations=a.citations, answer_confidence=a.confidence)


def _exec_events_in_window(con, params, as_of) -> ExecResult:
    from .timeline import timeline
    df = params.get("date_from")
    dt = params.get("date_to")
    if not (isinstance(df, str) and _ISO_DATE_RX.match(df)):
        return ExecResult("Give a start date (YYYY-MM-DD), e.g. --from 2026-07-01.",
                          ok=False, answer_confidence=0.0)
    date_from = date.fromisoformat(df)
    date_to = (date.fromisoformat(dt) if isinstance(dt, str) and _ISO_DATE_RX.match(dt)
               else date_from)
    pipes = None
    if params.get("pipeline"):
        pipes = [params["pipeline"]]
    t = timeline(con, date_from, date_to, pipelines=pipes, as_of=_as_of(params, as_of))
    cites = [f"evt:{e.event_uid}" for e in t.entries]
    return ExecResult(t.render(), citations=cites,
                      answer_confidence=1.0 if t.entries else 0.5)


def _exec_path_between(con, params, as_of) -> ExecResult:
    from .graph import build
    origin = str(params.get("origin") or "").strip()
    dest = str(params.get("destination") or "").strip()
    a = _resolve_node(con, origin)
    b = _resolve_node(con, dest)
    if a is None or b is None:
        bad = origin if a is None else dest
        return ExecResult(f"Couldn't resolve '{bad}' to a point, hub, or pipeline."
                          f" Use a point uid (C000307:519), a hub (HENRY), or a"
                          f" pipe code (SESH).", ok=False, answer_confidence=0.0)
    g = build(con)
    paths = g.paths(a, b)
    if not paths:
        return ExecResult(f"No commercial path found from {a} to {b} within the"
                          f" default hop/confidence bounds. They may connect via a"
                          f" counterparty pipe whose catalog isn't ingested.",
                          citations=[f"point:{a}", f"point:{b}"],
                          answer_confidence=0.0)
    best = paths[0]
    cites = [a, b] + sorted({e.citation for e in best.edges})
    return ExecResult(best.explain(), citations=cites,
                      answer_confidence=best.confidence)


def _exec_point_lookup(con, params, as_of) -> ExecResult:
    point = str(params.get("point") or "").strip()
    row = None
    if _POINT_UID_RX.match(point):
        row = con.execute("""
            SELECT point_uid, tsp_ferc_cid, loc, loc_name, loc_zone, loc_type_ind,
                   dir_flo, loc_stat_ind, pipeline_seg_cd
            FROM point WHERE point_uid = ?""", [point]).fetchone()
    if row is None and point:
        row = con.execute("""
            SELECT point_uid, tsp_ferc_cid, loc, loc_name, loc_zone, loc_type_ind,
                   dir_flo, loc_stat_ind, pipeline_seg_cd
            FROM point WHERE loc_name ILIKE ? ORDER BY point_uid LIMIT 1""",
            [f"%{point}%"]).fetchone()
    if row is None:
        return ExecResult(f"No point matches '{point}'. Give a point uid"
                          f" (C000307:4208) or a location name.", ok=False,
                          answer_confidence=0.0)
    (uid, tsp, loc, name, zone, ltype, dirflo, stat, seg) = row
    retired = " [RETIRED]" if stat == "I" else ""
    ics = con.execute("""
        SELECT b_tsp_ferc_cid, b_loc, b_name_posted, resolution_status
        FROM interconnect WHERE a_tsp_ferc_cid = ? AND a_loc = ?
        ORDER BY b_name_posted""", [tsp, loc]).fetchall()
    w = [f"{uid}{retired}  {name}",
         f"  zone {zone or '-'} · type {ltype or '-'} · dir {dirflo or '-'}"
         f" · seg {seg or '-'}"]
    if ics:
        w.append(f"  interconnects ({len(ics)}):")
        for (btsp, bloc, bname, rs) in ics:
            w.append(f"    -> {btsp}:{bloc or '?'} {bname or ''} [{rs}]")
    cites = [f"point:{uid}"] + [f"ic:{tsp}:{loc}->{b[0]}:{b[1]}" for b in ics]
    return ExecResult("\n".join(w), citations=[f"point:{uid}"], answer_confidence=1.0)


def _exec_contract_exposure(con, params, as_of) -> ExecResult:
    asset = str(params.get("asset") or "").strip()
    if asset:
        from .impact import _resolve_event
        euid = _resolve_event(con, asset)
        if euid is None:
            return ExecResult(f"No event matches '{asset}'.", ok=False,
                              answer_confidence=0.0)
        rows = con.execute("""
            SELECT h.contract_id, h.holder_name, h.mdq_dth, h.tsp_ferc_cid,
                   i.confidence, i.investigation
            FROM event_impact i JOIN contract_holding h ON h.holding_uid = i.subject_uid
            WHERE i.event_uid = ? AND i.subject_kind = 'holding'
            ORDER BY h.mdq_dth DESC""", [euid]).fetchall()
        if not rows:
            return ExecResult(f"No BP contract exposure identified for '{asset}'"
                              f" on the risk path.", citations=[f"evt:{euid}"],
                              answer_confidence=0.6)
        w = [f"BP contracts exposed by '{asset}':"]
        confs = []
        for (cid, holder, mdq, tsp, conf, inv) in rows:
            pipe = con.execute("SELECT coalesce(short_code,name) FROM pipeline"
                               " WHERE ferc_cid=?", [tsp]).fetchone()[0]
            w.append(f"  · {cid} ({holder}) {mdq:,} Dth/d on {pipe}  [conf {conf}]")
            w.append(f"    -> {inv}")
            confs.append(conf)
        return ExecResult("\n".join(w), citations=[f"evt:{euid}"],
                          answer_confidence=min(confs))
    # no asset -> list BP holdings
    rows = con.execute("""
        SELECT contract_id, holder_name, mdq_dth, tsp_ferc_cid
        FROM contract_holding WHERE holder_name ILIKE 'BP %' ORDER BY mdq_dth DESC
    """).fetchall()
    if not rows:
        return ExecResult("No BP contract holdings in the store.", ok=False,
                          answer_confidence=0.0)
    w = ["BP contract holdings:"]
    for (cid, holder, mdq, tsp) in rows:
        pipe = con.execute("SELECT coalesce(short_code,name) FROM pipeline"
                           " WHERE ferc_cid=?", [tsp]).fetchone()[0]
        w.append(f"  · {cid} ({holder}) {mdq:,} Dth/d on {pipe}")
    return ExecResult("\n".join(w),
                      citations=[f"holding:{r[0]}" for r in rows],
                      answer_confidence=1.0)


def _exec_interconnect_partners(con, params, as_of) -> ExecResult:
    point = str(params.get("point") or "").strip()
    pipe = str(params.get("pipeline") or "").strip()
    if _POINT_UID_RX.match(point):
        tsp, loc = point.split(":")
        rows = con.execute("""
            SELECT b_tsp_ferc_cid, b_loc, b_name_posted, resolution_status
            FROM interconnect WHERE a_tsp_ferc_cid = ? AND a_loc = ?
            ORDER BY b_name_posted""", [tsp, loc]).fetchall()
        head = f"Interconnects declared at {point}:"
    elif pipe:
        cid = con.execute("SELECT ferc_cid FROM pipeline WHERE upper(short_code)="
                          "upper(?) OR upper(ferc_cid)=upper(?)", [pipe, pipe]).fetchone()
        if cid is None:
            return ExecResult(f"Unknown pipeline '{pipe}'.", ok=False,
                              answer_confidence=0.0)
        rows = con.execute("""
            SELECT DISTINCT b_tsp_ferc_cid, coalesce(max(b_name_posted), ''),
                   min(resolution_status)
            FROM interconnect WHERE a_tsp_ferc_cid = ? AND b_tsp_ferc_cid IS NOT NULL
            GROUP BY b_tsp_ferc_cid ORDER BY b_tsp_ferc_cid""", [cid[0]]).fetchall()
        rows = [(r[0], None, r[1], r[2]) for r in rows]
        head = f"Counterparty pipelines CGT-style for {pipe}:"
    else:
        return ExecResult("Give a point uid (C000307:4208) or a pipeline (CGT).",
                          ok=False, answer_confidence=0.0)
    if not rows:
        return ExecResult(f"{head}\n  (none declared)", answer_confidence=0.6)
    w = [head]
    for (btsp, bloc, bname, rs) in rows:
        tgt = f"{btsp}:{bloc}" if bloc else btsp
        w.append(f"  · {tgt} {bname or ''} [{rs}]")
    return ExecResult("\n".join(w), answer_confidence=1.0)


def _exec_capacity_at_point(con, params, as_of) -> ExecResult:
    """HONEST STUB (§7.3): no OAC/operational-capacity data is loaded yet."""
    point = str(params.get("point") or "").strip() or "the point"
    return ExecResult(
        f"Operational capacity (scheduled vs operating) for {point} is not in the"
        f" engine — no OAC/OA_MLC screen has been ingested (public-data ceiling,"
        f" DDL-017). What I CAN tell you: its catalog attributes and interconnects"
        f" (ask 'look up point {point}'), and any active events on its segment"
        f" (ask 'what's happening on its pipe'). To get the number: pull the point's"
        f" OAC screen on the TSP EBB at the cycle you care about and compare"
        f" scheduled vs operating capacity.",
        answer_confidence=0.0, ok=True)


def _exec_storage_status(con, params, as_of) -> ExecResult:
    """HONEST STUB (§7.3): no storage-balance data is loaded (shipper-login only)."""
    facility = str(params.get("facility") or "").strip() or "the facility"
    return ExecResult(
        f"Storage balance / inventory for {facility} is not in the engine — storage"
        f" balances are behind BP's shipper login and are deliberately kept out of"
        f" this cloud configuration (DDL-012). What I CAN tell you: the storage"
        f" facility's interconnects and which portfolio pipes reach it (ask about"
        f" its point or path). For the balance itself, read it from the operator's"
        f" shipper portal manually.",
        answer_confidence=0.0, ok=True)


# ---- the registry ------------------------------------------------------------

REGISTRY: list[Intent] = [
    Intent(
        name="asset_impact",
        description="Assess the downstream impact, severity, exposed BP contracts,"
                    " and recommended investigations for a maintenance / force-majeure"
                    " / capacity event on a named asset (e.g. AlexSEG, Corinth).",
        params_schema={"asset": {"type": "string",
                                 "description": "asset or SEG name, e.g. 'AlexSEG'"},
                       "as_of": {"type": "string", "description": "gas day YYYY-MM-DD"}},
        required=["asset"],
        examples=["How does the AlexSEG maintenance affect SESH?",
                  "What does the Alexandria compressor station outage touch?",
                  "Assess the impact of the Corinth force majeure."],
        executor=_exec_asset_impact),
    Intent(
        name="events_in_window",
        description="List operational events (maintenance, constraints, FM) active or"
                    " upcoming across the portfolio in a date window, optionally one pipe.",
        params_schema={"date_from": {"type": "string", "description": "YYYY-MM-DD"},
                       "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                       "pipeline": {"type": "string", "description": "e.g. CGT"}},
        required=["date_from"],
        examples=["What's happening on CGT between 2026-07-01 and 2026-07-15?",
                  "Show me the events from 2026-07-05 to 2026-07-12.",
                  "What operational events are scheduled 2026-06-20 to 2026-06-30?"],
        executor=_exec_events_in_window),
    Intent(
        name="path_between",
        description="Find the cited commercial path (with flow direction and"
                    " min-composition confidence) between two points/hubs/pipelines.",
        params_schema={"origin": {"type": "string",
                                  "description": "point uid, hub, or pipe code"},
                       "destination": {"type": "string",
                                       "description": "point uid, hub, or pipe code"}},
        required=["origin", "destination"],
        examples=["How does gas get from C000307:519 to HENRY?",
                  "Show the path between C000307:4123 and C000086:45103.",
                  "Path from CGT 519 to Henry Hub."],
        executor=_exec_path_between),
    Intent(
        name="point_lookup",
        description="Look up a point's catalog attributes (zone, type, direction,"
                    " segment, retired flag) and its declared interconnects.",
        params_schema={"point": {"type": "string",
                                 "description": "point uid or location name"}},
        required=["point"],
        examples=["Look up point C000307:4208.",
                  "Tell me about the SESH Delhi interconnect point.",
                  "What is point C000307:519?"],
        executor=_exec_point_lookup),
    Intent(
        name="contract_exposure",
        description="Show BP firm-contract exposure — for a specific event's risk"
                    " path, or all BP holdings if no asset is given.",
        params_schema={"asset": {"type": "string",
                                 "description": "optional asset/SEG name"}},
        required=[],
        examples=["What BP contracts are exposed by the AlexSEG maintenance?",
                  "Show me BP's firm contracts.",
                  "Which BP contracts does the Alexandria outage put at risk?"],
        executor=_exec_contract_exposure),
    Intent(
        name="interconnect_partners",
        description="List the interconnect counterparties at a point, or the"
                    " counterparty pipelines a pipeline connects to.",
        params_schema={"point": {"type": "string", "description": "point uid"},
                       "pipeline": {"type": "string", "description": "pipe code"}},
        required=[],
        examples=["Who does CGT connect to at C000307:4208?",
                  "What are the interconnects at C000307:519?",
                  "List CGT's counterparty pipelines."],
        executor=_exec_interconnect_partners),
    Intent(
        name="capacity_at_point",
        description="Operational (scheduled vs operating) capacity at a point on a"
                    " gas day. STUB — no OAC data loaded; returns what to check.",
        params_schema={"point": {"type": "string", "description": "point uid"},
                       "as_of": {"type": "string", "description": "YYYY-MM-DD"}},
        required=["point"],
        examples=["What's the available capacity at C000307:4208 tomorrow?",
                  "How much operating capacity is on C000307:519 today?"],
        executor=_exec_capacity_at_point),
    Intent(
        name="storage_status",
        description="Storage balance / inventory at a storage facility. STUB — no"
                    " storage-balance data loaded (shipper-login only); returns what to check.",
        params_schema={"facility": {"type": "string",
                                    "description": "e.g. Egan, Bobcat"}},
        required=[],
        examples=["What's the Egan storage balance?",
                  "Show me the Bobcat storage inventory."],
        executor=_exec_storage_status),
]

BY_NAME = {i.name: i for i in REGISTRY}


def capability_list() -> str:
    """Human-readable list of what CAN be asked — used by the out-of-scope refusal."""
    return "\n".join(f"  · {i.name}: {i.description.split('.')[0]}."
                     f"  e.g. \"{i.examples[0]}\"" for i in REGISTRY)
