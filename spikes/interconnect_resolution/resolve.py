#!/usr/bin/env python3
"""
Entity-Resolution Spike — cross-pipeline interconnect resolution.

The resolution logic proven here was promoted to src/nge/resolve.py (Phase 2);
this spike remains as the runnable demonstration/report. See README.md alongside.

Run:  python3 spikes/interconnect_resolution/resolve.py
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "src"))

from nge.resolve import KNOWN_PIPELINES, load_points, resolve  # noqa: E402


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
