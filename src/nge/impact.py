"""Operational Impact Engine (OI-3.3; OI doc §2).

`assess()` answers the scheduler's first three questions about a constraint —
which of my points does this touch, is my firm service exposed, how bad is it —
as one typed, fully-cited ImpactAssessment: extracted facts (UNVERIFIED
flagged), the segment mapping used (with its evidence), materialized impact
rows grouped by reason, severity WITH its scoring components (`--explain` is
the default: no score without its reasons), a min-chain confidence roll-up,
and recommended investigations.

Subject resolution mirrors how a desk refers to things: an event's asset name
("Banner Compressor Station"), the notice idiom ("AlexSEG") via
segment_asset_map, or an asset_key. Time-aware: pass --as-of to see the event's
operational status on that gas day (bitemporal contract, OI doc §1.4).

Supersedes `nge.reach` as the impact entry point (reach remains as the pinned
Era-1 golden until OI-7 removes it).

Run:  PYTHONPATH=src python3 -m nge.impact --asset AlexSEG --as-of 2026-07-08
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date

import duckdb

from .events import status_at
from .severity import compute
from .store import DEFAULT_DB


@dataclass
class ImpactAssessment:
    asset: str
    event_uid: str
    event_name: str
    event_type: str
    pipeline: str
    status_at_as_of: str
    as_of: date
    window: tuple
    severity: str
    severity_components: list[str]
    facts: list[tuple]                    # (fact_uid, metric, lo, hi, uom, conf, verified)
    segment: tuple | None                 # (seg_cd, confidence, note)
    impacts_by_reason: dict[str, list]    # reason -> [(subject, hop, sev, conf, investigation, cites)]
    confidence: float                     # min over all cited impact rows
    citations: list[str]

    def render(self) -> str:
        w = []
        w.append(f"IMPACT ASSESSMENT — '{self.asset}' [{self.pipeline}]"
                 f"  as-of {self.as_of}")
        w.append("=" * 74)
        w.append(f"Event: {self.event_name} ({self.event_type})  [evt:{self.event_uid}]")
        w.append(f"       window {self.window[0]} → {self.window[1] or 'open'}"
                 f"  ·  status at {self.as_of}: {self.status_at_as_of.upper()}")
        w.append("")
        w.append(f"Severity: {self.severity.upper()}  (confidence {self.confidence})")
        for c in self.severity_components:
            w.append(f"  · {c}")
        w.append("")
        if self.facts:
            w.append("Extracted facts:")
            for fuid, metric, lo, hi, uom, conf, ver in self.facts:
                rng = f"{lo:,.0f}" + (f"–{hi:,.0f}" if hi != lo else "")
                flag = "verified" if ver else "UNVERIFIED"
                w.append(f"  · {metric}: {rng} {uom}  [fact:{fuid}, conf {conf}, {flag}]")
            w.append("")
        if self.segment:
            seg, conf, note = self.segment
            w.append(f"Mapped segment: {seg} (confidence {conf})  — {note}")
            w.append("")
        order = ("on_constrained_segment", "storage_service_at_risk",
                 "downstream_interconnect", "contract_at_affected_point",
                 "on_constrained_pipeline")
        for reason in order:
            rows = self.impacts_by_reason.get(reason, [])
            if not rows:
                continue
            w.append(f"{reason}  ({len(rows)}):")
            # on-segment rows are numerous; summarize, itemize the rest
            if reason == "on_constrained_segment" and len(rows) > 4:
                pts = ", ".join(r[0] for r in rows)
                retired = [r[0] for r in rows if "RETIRED" in r[4]]
                r0 = rows[0]
                w.append(f"  {pts}")
                w.append(f"  -> hop {r0[1]}, severity {r0[2]}, conf {r0[3]}")
                w.append(f"  -> Check the OAC screen for each at the next cycle;"
                         f" compare scheduled vs operating capacity.")
                if retired:
                    w.append(f"  -> RETIRED points included for completeness"
                             f" ({', '.join(retired)}) — verify successor points.")
            else:
                for subj, hop, sev, conf, inv, _cites in rows:
                    w.append(f"  · {subj}  (hop {hop}, {sev}, conf {conf})")
                    w.append(f"    -> {inv}")
            w.append("")
        w.append(f"Citations ({len(self.citations)}): " + " ".join(self.citations))
        return "\n".join(w)


def _resolve_event(con, asset: str):
    """Desk-style subject resolution: event asset name/key -> SEG idiom via
    segment_asset_map -> None."""
    row = con.execute("""
        SELECT event_uid FROM operational_event
        WHERE lower(asset_name) = lower(?) OR asset_key = lower(?)
        ORDER BY valid_from DESC NULLS LAST LIMIT 1
    """, [asset, asset]).fetchone()
    if row:
        return row[0]
    seg = con.execute(
        "SELECT tsp_ferc_cid, seg_cd FROM segment_asset_map"
        " WHERE lower(asset_name) = lower(?)", [asset]).fetchone()
    if seg:
        row = con.execute("""
            SELECT event_uid FROM operational_event
            WHERE tsp_ferc_cid = ? AND seg_cd = ?
            ORDER BY valid_from DESC NULLS LAST LIMIT 1
        """, list(seg)).fetchone()
        if row:
            return row[0]
    return None


def assess(con, asset: str, as_of: date | None = None) -> ImpactAssessment | None:
    as_of = as_of or date.today()
    evt_uid = _resolve_event(con, asset)
    if evt_uid is None:
        return None

    (tsp, etype, name, seg, life, vf, vt, notice_uids, _conf) = con.execute("""
        SELECT tsp_ferc_cid, event_type, asset_name, seg_cd, lifecycle_status,
               valid_from, valid_to, source_notice_uids, confidence
        FROM operational_event WHERE event_uid = ?""", [evt_uid]).fetchone()

    pipe = con.execute("SELECT coalesce(short_code, name) FROM pipeline"
                       " WHERE ferc_cid = ?", [tsp]).fetchone()[0]

    ph = ",".join("?" * len(notice_uids))
    facts = con.execute(f"""
        SELECT fact_uid, metric, value_low, value_high, uom, confidence,
               verified_by IS NOT NULL
        FROM capacity_impact_fact WHERE notice_uid IN ({ph}) ORDER BY metric
    """, list(notice_uids)).fetchall()

    segment = None
    if seg:
        segment = con.execute(
            "SELECT seg_cd, confidence, note FROM segment_asset_map"
            " WHERE tsp_ferc_cid = ? AND seg_cd = ?", [tsp, seg]).fetchone()

    # severity recomputed here WITH components (the propagation engine stores
    # only the result; the assessment explains it)
    design = next((f[2] for f in facts if f[1] == "design_capacity"), None)
    setting = next((f[2] for f in facts if f[1] == "estimated_capacity_setting"), None)
    services = con.execute(f"""
        SELECT affects_services FROM capacity_impact_fact
        WHERE notice_uid IN ({ph}) AND metric = 'estimated_capacity_setting'
    """, list(notice_uids)).fetchone()
    cut = (1 - setting / design) if (design and setting) else None
    sev, components = compute(
        event_type=etype, cut_pct=cut,
        affected_services=list(services[0]) if services else [],
        seg_mapped=seg is not None,
        facts_verified=any(f[6] for f in facts))

    rows = con.execute("""
        SELECT reason_code, subject_uid, hop_distance, severity, confidence,
               investigation, citations
        FROM event_impact WHERE event_uid = ?
        ORDER BY reason_code, subject_uid""", [evt_uid]).fetchall()
    by_reason: dict[str, list] = {}
    cites: list[str] = []
    confs: list[float] = []
    for reason, subj, hop, rsev, rconf, inv, rcites in rows:
        by_reason.setdefault(reason, []).append((subj, hop, rsev, rconf, inv, rcites))
        confs.append(rconf)
        for c in rcites:
            if c not in cites:
                cites.append(c)

    return ImpactAssessment(
        asset=asset, event_uid=evt_uid, event_name=name, event_type=etype,
        pipeline=pipe, status_at_as_of=status_at(vf, vt, life, as_of),
        as_of=as_of, window=(vf, vt), severity=sev,
        severity_components=components, facts=facts, segment=segment,
        impacts_by_reason=by_reason,
        confidence=min(confs) if confs else 0.0, citations=cites)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cited operational impact assessment")
    ap.add_argument("--asset", default="AlexSEG")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--db", default=DEFAULT_DB)
    ns = ap.parse_args()
    con = duckdb.connect(ns.db, read_only=True)
    try:
        a = assess(con, ns.asset, ns.as_of)
        if a is None:
            print(f"No operational event found for '{ns.asset}'. Known assets"
                  f" are listed by: SELECT asset_name FROM operational_event.")
        else:
            print(a.render())
    finally:
        con.close()


if __name__ == "__main__":
    main()
