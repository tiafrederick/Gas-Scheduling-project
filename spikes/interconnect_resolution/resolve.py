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

    # The headline proof: two confirmed confidence-1.0 round-trips across
    # different portfolio pipe pairs (CGT<->Egan, CGT<->Sabine).
    print("-" * 78)
    print("ROUND-TRIP PROOFS (confidence 1.0, both sides declare each other)")
    print("-" * 78)
    all_ok = True
    for a_uid, label in [("C000307:4123", "CGT 'EGAN STOR-ACADIA-REC' <-> Egan 'COLUMBIA - STORAGE'"),
                          ("C000307:519", "CGT 'SABINE - HENRY HUB' <-> Sabine 'Columbia Gulf - HH'")]:
        proof = next((e for e in edges if e.a_uid == a_uid), None)
        if not proof:
            print(f"  {label}: NOT FOUND"); all_ok = False; continue
        back = next((e for e in edges if e.a_uid == proof.b_uid), None)
        ok = proof.status == "resolved_roundtrip" and back is not None
        all_ok = all_ok and ok
        print(f"  {label}")
        print(f"    {proof.a_uid} -> {proof.b_uid}  [{proof.status}, conf {proof.confidence}]")
        if back:
            print(f"    {back.a_uid} -> {back.b_uid}  [{back.status}, conf {back.confidence}]")
        print(f"    {'PASS' if ok else 'FAIL'}")
    print(f"\n  RESULT : {'PASS - both round-trips resolved' if all_ok else 'FAIL'}")

    # A genuine finding, not a failure: SESH's own posting for this interconnect
    # still references CGT's retired, undifferentiated point (4208), not the split
    # delivery/receipt pair (4208D/4208R) CGT uses today. Surfaced, not hidden.
    print("\n" + "-" * 78)
    print("CROSS-EBB STALENESS FOUND: SESH 'COLUMBIA GULF - DELHI' (C001203:83004)")
    print("-" * 78)
    stale = next((e for e in edges if e.a_uid == "C001203:83004"), None)
    if stale:
        print(f"  SESH declares : {stale.a_uid} -> {stale.b_cid}:{stale.b_loc} (CGT's retired point)")
        cur = next((e for e in edges if e.a_uid == "C000307:4208D"), None)
        if cur:
            print(f"  CGT declares  : {cur.a_uid} -> {cur.b_cid}:{cur.b_loc} (the active replacement)")
        print(f"  status        : {stale.status}  (confidence {stale.confidence}, not 1.0 — correctly)")

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
