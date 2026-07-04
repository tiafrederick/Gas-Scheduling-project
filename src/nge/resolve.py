"""
Cross-pipeline interconnect resolution (DDL-002).

Promoted from spikes/interconnect_resolution/ once the round-trip proof passed.
Stdlib-only on purpose: the resolution logic is core enough that it must run
anywhere (tests, spikes, loader) without the analytical stack installed.

Each pipeline's point posting declares its counterparty's FERC CID + Loc. Keying
points by the composite (tsp_ferc_cid, loc) and resolving those declarations
yields the interconnect graph, with every edge classified by confidence tier.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# A source adapter is just a column map from canonical field -> source header.
# Two different EBB export layouts (SESH/InfoPost vs. Sabine/gasnom) already prove
# the "per-source adapter" architecture point (risk #7: schema drift).
SESH_LAYOUT = {
    "tsp_ferc_cid": "TSP FERC CID", "loc": "Loc", "loc_name": "Loc Name",
    "dir_flo": "Dir Flo", "updn_ind": "Up/Dn Ind",
    "updn_ferc_cid": "Up/Dn FERC CID", "updn_loc": "Up/Dn Loc",
    "updn_name": "Up/Dn Loc Name",
    # optional point metadata (loaded when present)
    "loc_st": "Loc St Abbrev", "loc_cnty": "Loc Cnty", "loc_zone": "Loc Zone",
    "loc_type_ind": "Loc Type Ind", "loc_stat_ind": "Loc Stat Ind",
}
SABINE_LAYOUT = {
    "tsp_ferc_cid": "TSP FERC CID", "loc": "LOC", "loc_name": "LOC NAME",
    "dir_flo": "DIR FLO", "updn_ind": "UP/DN IND",
    "updn_ferc_cid": "UP/DN FERC CID", "updn_loc": "UP/DN LOC",
    "updn_name": "UP/DN LOC NAME",
    "loc_st": "LOC ST ABBREV", "loc_cnty": "LOC CNTY", "loc_zone": "LOC ZONE",
    "loc_type_ind": "LOC TYPE IND", "loc_stat_ind": "LOC STAT IND",
}

# (relative path, layout, provenance label)
SOURCES = [
    ("data/samples/sesh_all_points.csv", SESH_LAYOUT, "SESH InfoPost (real)"),
    ("data/fixtures/cgt_points_sample.csv", SESH_LAYOUT, "CGT fixture (reciprocal of 83004)"),
    ("data/samples/sabine_locations.csv", SABINE_LAYOUT, "Sabine gasnom (real)"),
]

# Minimal company registry so we can recognise a declared counterparty pipeline
# even when we have not ingested its point catalog yet.
KNOWN_PIPELINES = {
    "C001203": "Southeast Supply Header, LLC (SESH)",
    "C000307": "Columbia Gulf Transmission, LLC (CGT)",
    "C000830": "Sabine Pipe Line LLC",
    "C000094": "Texas Eastern Transmission, LP (TETLP)",
    "C000654": "Transcontinental Gas Pipe Line (Transco)",
    "C000020": "Tennessee Gas Pipeline (TGP)",
    "C000021": "Southern Natural Gas (SNG)",
    "C000591": "Gulf South Pipeline",
    "C000255": "Florida Gas Transmission (FGT)",
    "C000087": "Gulfstream Natural Gas System",
    "C000544": "Enable Gas Transmission",
    "C001013": "ETC Tiger Pipeline",
    "C001199": "Mississippi Hub, LLC",
    "C001593": "SG Resources Mississippi, L.L.C.",
    "C000113": "Centana Intrastate Pipeline, LLC",
    "C000251": "Trunkline Gas Company, LLC",
    "C000433": "KM Texas Pipeline",
    "C000434": "KM Tejas Pipeline",
    "C001003": "Houston Pipe Line Company LP",
}

NULLISH = {"", "NA", "N/A", "NONE", "NULL"}


def clean(v: Optional[str]) -> Optional[str]:
    """Strip tabs/whitespace/quotes; map NA-like tokens to None."""
    if v is None:
        return None
    v = v.replace("\t", "").strip().strip('"').strip()
    return None if v.upper() in NULLISH else v


@dataclass
class Point:
    tsp_ferc_cid: str
    loc: str
    loc_name: Optional[str]
    dir_flo: Optional[str]
    updn_ind: Optional[str]
    updn_ferc_cid: Optional[str]
    updn_loc: Optional[str]
    updn_name: Optional[str]
    source: str
    source_file: Optional[str] = None
    loc_st: Optional[str] = None
    loc_cnty: Optional[str] = None
    loc_zone: Optional[str] = None
    loc_type_ind: Optional[str] = None
    loc_stat_ind: Optional[str] = None

    @property
    def uid(self) -> str:
        return f"{self.tsp_ferc_cid}:{self.loc}"


@dataclass
class Edge:
    a_uid: str
    a_cid: str
    a_loc: str
    b_cid: Optional[str]
    b_loc: Optional[str]
    b_name: Optional[str]
    dir_flo: Optional[str]
    status: str = ""
    confidence: float = 0.0
    b_uid: Optional[str] = None
    note: str = ""
    source_file: Optional[str] = None


def load_points(repo: str = REPO) -> tuple[dict[str, Point], list[Point]]:
    catalog: dict[str, Point] = {}
    ordered: list[Point] = []
    for rel, layout, label in SOURCES:
        path = os.path.join(repo, rel)
        if not os.path.exists(path):
            print(f"  ! missing source: {rel}")
            continue
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                cid = clean(row.get(layout["tsp_ferc_cid"]))
                loc = clean(row.get(layout["loc"]))
                if not cid or not loc:
                    continue
                p = Point(
                    tsp_ferc_cid=cid, loc=loc,
                    loc_name=clean(row.get(layout["loc_name"])),
                    dir_flo=clean(row.get(layout["dir_flo"])),
                    updn_ind=clean(row.get(layout["updn_ind"])),
                    updn_ferc_cid=clean(row.get(layout["updn_ferc_cid"])),
                    updn_loc=clean(row.get(layout["updn_loc"])),
                    updn_name=clean(row.get(layout["updn_name"])),
                    source=label,
                    source_file=rel,
                    loc_st=clean(row.get(layout.get("loc_st", ""))),
                    loc_cnty=clean(row.get(layout.get("loc_cnty", ""))),
                    loc_zone=clean(row.get(layout.get("loc_zone", ""))),
                    loc_type_ind=clean(row.get(layout.get("loc_type_ind", ""))),
                    loc_stat_ind=clean(row.get(layout.get("loc_stat_ind", ""))),
                )
                catalog[p.uid] = p
                ordered.append(p)
    return catalog, ordered


def resolve(points: list[Point], catalog: dict[str, Point]) -> list[Edge]:
    edges: list[Edge] = []
    for p in points:
        e = Edge(a_uid=p.uid, a_cid=p.tsp_ferc_cid, a_loc=p.loc,
                 b_cid=p.updn_ferc_cid, b_loc=p.updn_loc, b_name=p.updn_name,
                 dir_flo=p.dir_flo, source_file=p.source_file)

        if not p.updn_ferc_cid and not p.updn_loc:
            e.status, e.confidence = "unresolved_no_counterparty", 0.0
            e.note = "endpoint / intra-system (no Up/Dn keys posted)"
            edges.append(e); continue

        b_uid = f"{p.updn_ferc_cid}:{p.updn_loc}" if (p.updn_ferc_cid and p.updn_loc) else None
        counterparty = catalog.get(b_uid) if b_uid else None

        if counterparty is not None:
            # Round-trip: does the counterparty point mirror back to us?
            mirror = (counterparty.updn_ferc_cid == p.tsp_ferc_cid
                      and counterparty.updn_loc == p.loc)
            e.b_uid = b_uid
            if mirror:
                e.status, e.confidence = "resolved_roundtrip", 1.0
                e.note = "both sides mirror — highest confidence"
            else:
                e.status, e.confidence = "resolved_cid_loc", 0.9
                e.note = "counterparty point present; no reciprocal mirror"
        elif p.updn_ferc_cid in KNOWN_PIPELINES:
            e.status, e.confidence = "resolved_cid_only", 0.6
            e.note = f"counterparty pipe known ({KNOWN_PIPELINES[p.updn_ferc_cid]}); point not yet ingested"
        elif p.updn_ferc_cid:
            e.status, e.confidence = "declared_external", 0.4
            e.note = "counterparty CID declared but unknown/uncatalogued"
        else:
            e.status, e.confidence = "unresolved", 0.0
            e.note = "counterparty loc present but no FERC CID"
        edges.append(e)
    return edges
