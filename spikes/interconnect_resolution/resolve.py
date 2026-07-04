#!/usr/bin/env python3
"""
Entity-Resolution Spike — cross-pipeline interconnect resolution.

THESIS (Phase 1, DDL-002/008): the cross-pipeline graph is latent in the point
data. Each pipeline's point row declares its counterparty's FERC CID + Loc. If we
key points by the composite (tsp_ferc_cid, loc) and resolve each declared
counterparty against a point catalog, we can walk "CGT event -> SESH delivery"
WITHOUT any manual mapping — provided resolution is done carefully and every edge
is classified by confidence.

This spike is intentionally zero-dependency (stdlib only) and reads real public
FERC informational postings (SESH) plus a one-row CGT fixture that is the reciprocal
of SESH point 83004 (pending a full parse of CGT Location Data.pdf). It proves the
resolution LOGIC and the 83004<->4208 round-trip; it is NOT the production pipeline.

Run:  python3 spikes/interconnect_resolution/resolve.py
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
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
}
SABINE_LAYOUT = {
    "tsp_ferc_cid": "TSP FERC CID", "loc": "LOC", "loc_name": "LOC NAME",
    "dir_flo": "DIR FLO", "updn_ind": "UP/DN IND",
    "updn_ferc_cid": "UP/DN FERC CID", "updn_loc": "UP/DN LOC",
    "updn_name": "UP/DN LOC NAME",
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


def load_points() -> tuple[dict[str, Point], list[Point]]:
    catalog: dict[str, Point] = {}
    ordered: list[Point] = []
    for rel, layout, label in SOURCES:
        path = os.path.join(REPO, rel)
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
                )
                catalog[p.uid] = p
                ordered.append(p)
    return catalog, ordered


def resolve(points: list[Point], catalog: dict[str, Point]) -> list[Edge]:
    edges: list[Edge] = []
    for p in points:
        e = Edge(a_uid=p.uid, a_cid=p.tsp_ferc_cid, a_loc=p.loc,
                 b_cid=p.updn_ferc_cid, b_loc=p.updn_loc, b_name=p.updn_name,
                 dir_flo=p.dir_flo)

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


def main() -> None:
    print("=" * 78)
    print("INTERCONNECT RESOLUTION SPIKE")
    print("=" * 78)
    catalog, points = load_points()
    print(f"Loaded {len(points)} points into catalog ({len(catalog)} unique uids)\n")

    edges = resolve(points, catalog)

    # Summary by resolution tier
    tiers: dict[str, int] = {}
    for e in edges:
        tiers[e.status] = tiers.get(e.status, 0) + 1
    print("Resolution tiers:")
    for status, n in sorted(tiers.items(), key=lambda kv: -kv[1]):
        print(f"  {status:28s} {n:4d}")
    print()

    # The headline proof: SESH 83004 <-> CGT 4208 round-trip
    print("-" * 78)
    print("ROUND-TRIP PROOF: SESH 'COLUMBIA GULF - DELHI' (C001203:83004)")
    print("-" * 78)
    proof = next((e for e in edges if e.a_uid == "C001203:83004"), None)
    if proof:
        print(f"  A side : {proof.a_uid}  ({proof.dir_flo})")
        print(f"  B side : {proof.b_cid}:{proof.b_loc}  '{proof.b_name}'")
        print(f"  status : {proof.status}  (confidence {proof.confidence})")
        print(f"  note   : {proof.note}")
        back = next((e for e in edges if e.a_uid == "C000307:4208"), None)
        if back:
            print(f"  reverse: {back.a_uid} -> {back.b_cid}:{back.b_loc}  "
                  f"[{back.status}]")
        ok = proof.status == "resolved_roundtrip"
        print(f"\n  RESULT : {'PASS - bidirectional interconnect resolved' if ok else 'FAIL'}")
    else:
        print("  SESH 83004 not found — check data/samples/sesh_all_points.csv")

    # Show a few representative external declarations (why we must ingest more pipes)
    print("\n" + "-" * 78)
    print("SAMPLE OF SESH INTERCONNECTS DECLARING EXTERNAL PIPELINES")
    print("(these become resolved once we ingest the counterparty's point catalog)")
    print("-" * 78)
    shown = 0
    for e in edges:
        if e.a_cid == "C001203" and e.status == "resolved_cid_only":
            print(f"  {e.a_uid:16s} -> {e.b_cid}:{e.b_loc:12s} {e.note}")
            shown += 1
            if shown >= 8:
                break


if __name__ == "__main__":
    main()
