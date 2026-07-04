"""Load the canonical store from landed samples/fixtures (Phase 2).

Sources loaded (all public FERC informational-postings data, DDL-007):
  pipeline              seed registry incl. portfolio assets + counterparties
  point                 data/samples/*.csv via nge.resolve source adapters
  interconnect          resolver output with confidence tiers
  contract_holding/_point  SESH Index of Customers (H/D/P hierarchy)
  notice + capacity_impact_fact  AlexSEG gold fixture (manual extraction)

Run:  PYTHONPATH=src python3 -m nge.load
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime

from . import resolve as R
from .store import DEFAULT_DB, create_db, table_counts

REPO = R.REPO

# Portfolio registry. Egan/Bobcat FERC CIDs backfilled 2026-07-04 from their real
# InfoPost point-location CSVs (landed via Firecrawl; see data/raw/{egan,bobcat}/
# 2026-07-04/*.meta.json for provenance).
PIPELINES = [
    # (ferc_cid, tsp_id, name, short_code, platform, base_url, is_portfolio)
    ("C000307", "007854581", "Columbia Gulf Transmission, LLC", "CGT",
     "TC eConnects", "https://ebb.tceconnects.com", True),
    ("C001203", "808264746", "Southeast Supply Header, LLC", "SESH",
     "Enbridge InfoPost", "https://infopost.enbridge.com/infopost/SESHHome.asp?Pipe=SESH", True),
    ("C000830", "137609871", "Sabine Pipe Line LLC", "SABINE",
     "gasnom", "https://www.gasnom.com/ip/SABINE/", True),
    ("C000086", "835460478", "Egan Hub Storage, LLC", "EGAN",
     "Enbridge InfoPost", "https://infopost.enbridge.com/infopost/EGHome.asp?Pipe=EG", True),
    ("C001706", "614834559", "Bobcat Gas Storage", "BOBCAT",
     "Enbridge InfoPost", "https://infopost.enbridge.com/infopost/BGSHome.asp?Pipe=BGS", True),
]

IOC_CSV = os.path.join(REPO, "data", "samples", "sesh_index_of_customers.csv")
GOLD_JSON = os.path.join(REPO, "data", "fixtures", "cgt_notice_26092015.expected_facts.json")


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def _date(s: str | None) -> str | None:
    """mm/dd/yyyy -> ISO, else pass through/None."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return s


def load_pipelines(con) -> None:
    for cid, tsp_id, name, code, platform, url, portfolio in PIPELINES:
        con.execute(
            "INSERT INTO pipeline (ferc_cid, tsp_id, name, short_code, ebb_platform,"
            " ebb_base_url, is_portfolio) VALUES (?,?,?,?,?,?,?)",
            [cid, tsp_id, name, code, platform, url, portfolio])
    # Counterparty pipes referenced by interconnects (name-only rows, not portfolio)
    for cid, name in R.KNOWN_PIPELINES.items():
        if not con.execute("SELECT 1 FROM pipeline WHERE ferc_cid=?", [cid]).fetchone():
            con.execute(
                "INSERT INTO pipeline (ferc_cid, name, is_portfolio) VALUES (?,?,FALSE)",
                [cid, name])


def load_points_and_interconnects(con) -> None:
    catalog, points = R.load_points()
    for p in points:
        con.execute(
            "INSERT INTO point (point_uid, tsp_ferc_cid, loc, loc_name, loc_st_abbrev,"
            " loc_cnty, loc_zone, loc_type_ind, dir_flo, loc_stat_ind, source_file)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [p.uid, p.tsp_ferc_cid, p.loc, p.loc_name, p.loc_st, p.loc_cnty,
             p.loc_zone, p.loc_type_ind, p.dir_flo, p.loc_stat_ind, p.source_file])
    for e in R.resolve(points, catalog):
        con.execute(
            "INSERT INTO interconnect (interconnect_uid, a_point_uid, a_tsp_ferc_cid,"
            " a_loc, b_point_uid, b_tsp_ferc_cid, b_loc, b_name_posted, dir_flo,"
            " resolution_status, resolution_confidence, source_file)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [_uid(e.a_uid, e.b_cid or "", e.b_loc or ""), e.a_uid, e.a_cid, e.a_loc,
             e.b_uid, e.b_cid, e.b_loc, e.b_name, e.dir_flo,
             e.status, e.confidence, e.source_file])


def load_index_of_customers(con) -> None:
    """Parse the FERC Index of Customers H/D/P hierarchy (SESH layout).

    H = TSP header (carries the pipeline FERC CID)
    D = contract:  [1]=holder [2]=holder_id [4]=rate schedule [5]=contract id
                   [6]=term start [7]=term end [9]=neg rate flag [10]=MDQ
    P = point row: [2]=loc name [4]=loc [6]=qty  (attached to the last D seen)
    """
    tsp_cid, holding_uid = None, None
    with open(IOC_CSV, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            rectype = row[0].strip()
            if rectype == "H" and len(row) > 2:
                tsp_cid = row[2].strip()
            elif rectype == "D" and len(row) > 10 and tsp_cid:
                contract_id = row[5].strip()
                holding_uid = _uid(tsp_cid, contract_id, row[1], row[6])
                con.execute(
                    "INSERT INTO contract_holding (holding_uid, tsp_ferc_cid,"
                    " holder_name, holder_id, rate_schedule, contract_id,"
                    " neg_rate_ind, mdq_dth, term_start, term_end, source_file)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [holding_uid, tsp_cid, row[1].strip(), row[2].strip(),
                     row[4].strip(), contract_id, row[9].strip() or None,
                     int(row[10]) if row[10].strip().isdigit() else None,
                     _date(row[6]), _date(row[7]),
                     "data/samples/sesh_index_of_customers.csv"])
            elif rectype == "P" and len(row) > 6 and holding_uid:
                loc = row[4].strip()
                point_uid = f"{tsp_cid}:{loc}"
                exists = con.execute("SELECT 1 FROM point WHERE point_uid=?",
                                     [point_uid]).fetchone()
                con.execute(
                    "INSERT INTO contract_point (holding_uid, point_uid, loc,"
                    " loc_name, rec_del, qty_dth) VALUES (?,?,?,?,?,?)",
                    [holding_uid, point_uid if exists else None, loc,
                     row[2].strip(), row[1].strip(),
                     int(row[6]) if row[6].strip().isdigit() else None])


def load_notice_and_facts(con) -> None:
    with open(GOLD_JSON, encoding="utf-8") as fh:
        gold = json.load(fh)
    n = gold["notice"]
    with open(os.path.join(REPO, n["source_file"]), encoding="utf-8") as bf:
        body = bf.read()
    notice_uid = f"{n['tsp_ferc_cid']}:{n['notice_id']}"
    con.execute(
        "INSERT INTO notice (notice_uid, tsp_ferc_cid, notice_id, notice_type,"
        " notice_stat_desc, critical, subject, author, body_text, post_dt,"
        " effective_dt, end_dt, prior_notice_id, source_file)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [notice_uid, n["tsp_ferc_cid"], n["notice_id"], n["notice_type"],
         n["notice_stat_desc"], n["critical"], n["subject"], n["author"], body,
         n["post_dt"], n["effective_dt"], n["end_dt"], n["prior_notice_id"],
         n["source_file"]])
    for f in gold["capacity_impact_facts"]:
        con.execute(
            "INSERT INTO capacity_impact_fact (fact_uid, notice_uid, tsp_ferc_cid,"
            " asset_name, asset_kind, direction, metric, value_low, value_high, uom,"
            " valid_gas_day_from, valid_gas_day_to, effective_cycle, affects_services,"
            " source_span, extraction_method, confidence)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [_uid(f["notice_uid"], f["metric"], f["source_span"]), f["notice_uid"],
             f["tsp_ferc_cid"], f["asset_name"], f["asset_kind"], f.get("direction"),
             f["metric"], f["value_low"], f["value_high"], f["uom"],
             f.get("valid_gas_day_from"), f.get("valid_gas_day_to"),
             f.get("effective_cycle"), f.get("affects_services", []),
             f["source_span"], f["extraction_method"], f["confidence"]])


def load_all(db_path: str = DEFAULT_DB):
    con = create_db(db_path, fresh=True)
    load_pipelines(con)
    load_points_and_interconnects(con)
    load_index_of_customers(con)
    load_notice_and_facts(con)
    return con


def main() -> None:
    con = load_all()
    print("Canonical store loaded:", DEFAULT_DB)
    for table, n in table_counts(con).items():
        print(f"  {table:28s} {n:5d} rows")
    con.close()


if __name__ == "__main__":
    main()
