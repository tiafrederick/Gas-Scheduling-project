"""Operational Timeline (OI-4; OI doc §5).

One chronological, status-aware view of everything happening across the
portfolio — the desk-handover question ("what's on this week?") and the
post-mortem question ("what did we KNOW Friday morning?") answered by the same
API. Read-only over the event/impact projections: reading history never
mutates it.

Bitemporal contract (OI doc §1.4):
  as_of     — the gas day whose operational status you want
              (planned/active/completed via events.status_at, a pure function)
  as_known  — reconstruct the timeline from ONLY the notices posted by that
              timestamp. Not a filter on today's answers: the notice subset is
              re-folded through the SAME pure chain logic derive_events uses
              (events.fold_notices/finalize_chains), so a chain mid-life shows
              its shape AT THAT MOMENT — East Lateral on June 25 is 'updated'
              with a June-25 end, because the COMPLETED notice didn't exist yet.

Run:  PYTHONPATH=src python3 -m nge.timeline --from 2026-06-01 --to 2026-07-15
      PYTHONPATH=src python3 -m nge.timeline --from 2026-07-01 --to 2026-07-10 \
          --as-known 2026-07-02T00:00
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from datetime import date, datetime

import duckdb

from .events import finalize_chains, fold_notices, status_at
from .severity import rank
from .store import DEFAULT_DB


@dataclass
class TimelineEntry:
    event_uid: str
    pipe: str
    asset_name: str
    event_type: str
    seg_cd: str | None
    lifecycle_status: str
    valid_from: date | None
    valid_to: date | None
    window_source: str
    confidence: float
    severity: str | None            # hop-0 severity; None for superseded / as-known views
    status_at_as_of: str
    n_sources: int
    superseded_by_uid: str | None = None

    def line(self) -> str:
        # str() first: date.__format__ treats '<10' as an strftime pattern
        end = str(self.valid_to) if self.valid_to else "open"
        win = (f"{self.valid_from} → {end:<10}"
               if self.valid_from else f"{'window unknown':<23}")
        sev = self.severity or "-"
        extra = ""
        if self.lifecycle_status == "superseded":
            extra = f"  << superseded by {self.superseded_by_uid}"
        elif self.n_sources > 1:
            extra = f"  ({self.n_sources} notices)"
        return (f"{win}  [{self.pipe}] {self.asset_name:<45.45s}"
                f" {self.event_type:<19s} {self.status_at_as_of.upper():<11s}"
                f" {sev:<13s} conf {self.confidence:.2f}{extra}")


@dataclass
class Timeline:
    entries: list[TimelineEntry]
    date_from: date
    date_to: date
    as_of: date
    as_known: datetime | None

    def render(self) -> str:
        knowledge = (f"as-known {self.as_known.isoformat()}"
                     if self.as_known else "knowledge: current")
        head = (f"OPERATIONAL TIMELINE  {self.date_from} → {self.date_to}"
                f"   (as-of {self.as_of}, {knowledge})")
        lines = [head, "=" * len(head)]
        if not self.entries:
            lines.append("(no events in window at these filters)")
        lines.extend(e.line() for e in self.entries)
        return "\n".join(lines)


def _overlaps(vf, vt, date_from: date, date_to: date) -> bool:
    """Window intersects [date_from, date_to]; open ends stay open, and an
    unknown window is SHOWN (hiding what we can't date would be a silent miss)."""
    if vf is None and vt is None:
        return True
    if vf is not None and vf > date_to:
        return False
    if vt is not None and vt < date_from:
        return False
    return True


def timeline(con, date_from: date, date_to: date, pipelines=None, assets=None,
             min_severity: str | None = None, as_of: date | None = None,
             as_known: datetime | None = None) -> Timeline:
    as_of = as_of or date.today()

    pipe_codes = {r[0]: (r[1] or r[0]) for r in con.execute(
        "SELECT ferc_cid, short_code FROM pipeline").fetchall()}
    cid_filter = None
    if pipelines:
        wanted = {p.upper() for p in pipelines}
        cid_filter = {cid for cid, code in pipe_codes.items()
                      if code.upper() in wanted or cid.upper() in wanted}

    if as_known is None:
        rows = con.execute("""
            SELECT e.event_uid, e.tsp_ferc_cid, e.asset_name, e.event_type,
                   e.seg_cd, e.lifecycle_status, e.valid_from, e.valid_to,
                   e.window_source, e.confidence, len(e.source_notice_uids),
                   (SELECT max(severity ORDER BY
                        CASE severity WHEN 'critical' THEN 4 WHEN 'action' THEN 3
                        WHEN 'watch' THEN 2 ELSE 1 END)
                    FROM event_impact i
                    WHERE i.event_uid = e.event_uid AND i.hop_distance = 0),
                   (SELECT s.event_uid FROM operational_event s
                    WHERE s.supersedes_event_uid = e.event_uid)
            FROM operational_event e ORDER BY e.event_uid
        """).fetchall()
        raw = [dict(event_uid=r[0], tsp=r[1], name=r[2], etype=r[3], seg=r[4],
                    lifecycle=r[5], vf=r[6], vt=r[7], wsrc=r[8], conf=r[9],
                    n_src=r[10], severity=r[11], superseded_by=r[12])
               for r in rows]
    else:
        # AS-KNOWN reconstruction: refold from the notices posted by then.
        notices = con.execute("""
            SELECT notice_uid, tsp_ferc_cid, notice_id, notice_type, subject,
                   effective_dt
            FROM notice WHERE post_dt <= ? ORDER BY post_dt, notice_id
        """, [as_known]).fetchall()
        known_uids = [n[0] for n in notices]
        fact_windows = {}
        if known_uids:
            ph = ",".join("?" * len(known_uids))
            fact_windows = {r[0]: (r[1], r[2]) for r in con.execute(f"""
                SELECT notice_uid, min(valid_gas_day_from), max(valid_gas_day_to)
                FROM capacity_impact_fact
                WHERE valid_gas_day_from IS NOT NULL AND notice_uid IN ({ph})
                GROUP BY notice_uid""", known_uids).fetchall()}
        events = finalize_chains(fold_notices(notices, fact_windows))
        seg_by_uid = {r[0]: r[1] for r in con.execute(
            "SELECT event_uid, seg_cd FROM operational_event").fetchall()}
        superseded_by = {e["supersedes_uid"]: e["event_uid"]
                         for e in events if e["supersedes_uid"]}
        raw = [dict(event_uid=e["event_uid"], tsp=e["tsp"], name=e["name"],
                    etype=e["event_type"], seg=seg_by_uid.get(e["event_uid"]),
                    lifecycle=e["lifecycle"], vf=e["from"], vt=e["to"],
                    wsrc=e["window_source"], conf=e["confidence"],
                    n_src=len(e["sources"]),
                    severity=None,           # severity is knowledge-current; not recomputed here
                    superseded_by=superseded_by.get(e["event_uid"]))
               for e in events]

    entries: list[TimelineEntry] = []
    for r in raw:
        if cid_filter and r["tsp"] not in cid_filter:
            continue
        if assets and not any(a.lower() in r["name"].lower() for a in assets):
            continue
        if not _overlaps(r["vf"], r["vt"], date_from, date_to):
            continue
        if min_severity:
            if r["severity"] is None or rank(r["severity"]) < rank(min_severity):
                continue
        entries.append(TimelineEntry(
            event_uid=r["event_uid"], pipe=pipe_codes.get(r["tsp"], r["tsp"]),
            asset_name=r["name"], event_type=r["etype"], seg_cd=r["seg"],
            lifecycle_status=r["lifecycle"], valid_from=r["vf"], valid_to=r["vt"],
            window_source=r["wsrc"], confidence=r["conf"], severity=r["severity"],
            status_at_as_of=status_at(r["vf"], r["vt"], r["lifecycle"], as_of),
            n_sources=r["n_src"], superseded_by_uid=r["superseded_by"]))

    entries.sort(key=lambda e: (
        e.valid_from or date.max,
        -(rank(e.severity) if e.severity else -1),
        e.asset_name.lower()))
    return Timeline(entries=entries, date_from=date_from, date_to=date_to,
                    as_of=as_of, as_known=as_known)


# ---- brief_run bookkeeping (written by the brief generator, OI-5) ------------

def record_brief_run(con, as_of: date, date_from: date, date_to: date) -> str:
    run_at = datetime.now()
    run_uid = hashlib.sha1(f"brief|{run_at.isoformat()}".encode()).hexdigest()[:16]
    con.execute("INSERT INTO brief_run (run_uid, run_at, as_of, date_from,"
                " date_to) VALUES (?,?,?,?,?)",
                [run_uid, run_at, as_of, date_from, date_to])
    return run_uid


def last_brief_run(con) -> datetime | None:
    row = con.execute("SELECT max(run_at) FROM brief_run").fetchone()
    return row[0] if row and row[0] else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Chronological, bitemporal"
                                             " operational view")
    ap.add_argument("--from", dest="date_from", type=date.fromisoformat,
                    required=True)
    ap.add_argument("--to", dest="date_to", type=date.fromisoformat,
                    required=True)
    ap.add_argument("--pipe", action="append", dest="pipelines")
    ap.add_argument("--asset", action="append", dest="assets")
    ap.add_argument("--min-severity", choices=("informational", "watch",
                                               "action", "critical"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--as-known", type=datetime.fromisoformat, default=None)
    ap.add_argument("--db", default=DEFAULT_DB)
    ns = ap.parse_args()
    con = duckdb.connect(ns.db, read_only=True)
    try:
        print(timeline(con, ns.date_from, ns.date_to, ns.pipelines, ns.assets,
                       ns.min_severity, ns.as_of, ns.as_known).render())
    finally:
        con.close()


if __name__ == "__main__":
    main()
