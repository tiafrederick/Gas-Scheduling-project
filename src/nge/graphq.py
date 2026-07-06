"""Query the Pipeline Relationship Graph from the command line.

A thin wrapper over the service facade (OI-7): every operation goes through
`nge.api.Engine`, which owns the connection and the cached graph build.

Run:  PYTHONPATH=src python3 -m nge.graphq stats
      PYTHONPATH=src python3 -m nge.graphq neighbors C000307:519
      PYTHONPATH=src python3 -m nge.graphq paths C000307:519 hub:HENRY
      PYTHONPATH=src python3 -m nge.graphq explain C000307:4123 C000086:45103
"""
from __future__ import annotations

import argparse

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

    from .api import Engine
    with Engine(ns.db) as e:
        if ns.command == "stats":
            print(e.graph_stats().render())
        elif ns.command == "neighbors":
            (uid,) = ns.args
            print(e.neighbors(uid, min_conf=0.0).render())
        else:                                   # "paths" | "explain" (same view)
            a, b = ns.args
            print(e.path(a, b, max_hops=ns.max_hops, min_conf=ns.min_conf,
                         include_inactive=ns.include_inactive,
                         limit=ns.limit).render())


if __name__ == "__main__":
    main()
