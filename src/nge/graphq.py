"""Query the Pipeline Relationship Graph from the command line.

Run:  PYTHONPATH=src python3 -m nge.graphq stats
      PYTHONPATH=src python3 -m nge.graphq neighbors C000307:519
      PYTHONPATH=src python3 -m nge.graphq paths C000307:519 hub:HENRY
      PYTHONPATH=src python3 -m nge.graphq explain C000307:4123 C000086:45103
"""
from __future__ import annotations

import argparse

import duckdb

from .graph import build
from .store import DEFAULT_DB


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline Relationship Graph queries")
    ap.add_argument("command", choices=["stats", "neighbors", "paths", "explain"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--min-conf", type=float, default=0.7)
    ap.add_argument("--include-inactive", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ns = ap.parse_args()

    con = duckdb.connect(ns.db, read_only=True)
    try:
        g = build(con)
        if ns.command == "stats":
            for k, v in g.stats().items():
                print(f"  {k:24s} {v:5d}")
        elif ns.command == "neighbors":
            (uid,) = ns.args
            node = g.node(uid)
            print(f"{node.label} [{node.kind}]"
                  + ("" if node.active else "  [RETIRED]"))
            for e in g.neighbors(uid, min_conf=0.0):
                dst = g.nodes[e.dst]
                lead = "  (lead only)" if "lead" in e.note else ""
                stale = "" if dst.active else "  [RETIRED]"
                print(f"  {e.flow_wording():20s} {dst.label} [{dst.kind}]"
                      f"  conf {e.confidence:.2f} via {e.kind}"
                      f" (cite {e.citation}){lead}{stale}")
        elif ns.command in ("paths", "explain"):
            a, b = ns.args
            results = g.paths(a, b, max_hops=ns.max_hops, min_conf=ns.min_conf,
                              active_only=not ns.include_inactive, limit=ns.limit)
            if not results:
                print(f"No paths {a} -> {b} within {ns.max_hops} hops at"
                      f" confidence >= {ns.min_conf}. Try --min-conf 0.6 to"
                      f" include leads, or --include-inactive.")
            for i, p in enumerate(results, 1):
                print(f"--- option {i} ---")
                print(p.explain())
    finally:
        con.close()


if __name__ == "__main__":
    main()
