"""Morning Brief generator (OI-5; OI doc §6; DDL-018).

The first 30 minutes of a scheduler's day is triage: what posted overnight, what
is live today, what threatens today's nominations. The brief compresses that to
one ranked, cited page, calibrated so everything above the fold is worth reading
before the Timely deadline.

Four stages, deterministic core first (DDL-014):

  assemble  — non-superseded events that are ACTIVE on the as-of gas day, or
              PLANNED within a short look-ahead, or NEW since the last brief_run
              (novelty). Each joined to its materialized event_impact rows.
  rank      — an explainable score (DDL-018):
                  severity_weight x exposure_factor x novelty x confidence
              every component printed in --explain mode. No score without its
              reasons.
  render    — a markdown template, byte-stable and golden-tested. Sections
              Critical / Action / Watch / FYI / Data-quality notes; the last is
              not decorative — it standingly surfaces the SESH->4208 cross-EBB
              staleness, unmapped force-majeure assets, and unverified figures.
  polish    — OPTIONAL. If a narrator callable is supplied, its prose is run
              through the citation gate (nge.citegate, §1.2); it ships only if
              every sentence is cited to a uid in the input bundle, else the
              template text ships unchanged. Template mode carries zero LLM
              artifacts (the no-key contract, OI doc §1.5).

Run:  PYTHONPATH=src python3 -m nge.brief --as-of 2026-07-05 [--explain] [--no-llm]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import duckdb

from . import citegate
from .events import status_at
from .severity import SEVERITY_ORDER, rank
from .store import DEFAULT_DB
from .timeline import last_brief_run, record_brief_run

# ---- the ranking constants block (reviewable hypotheses, DDL-018) ------------
# Change a weight and the golden brief diff shows you exactly what re-ranks.

SEVERITY_WEIGHT = {                 # relative "worth reading first" weight
    "critical": 8.0,
    "action": 4.0,
    "watch": 2.0,
    "informational": 1.0,
}

# exposure_factor scales the score with how much BP FIRM capacity (Dth/d) the
# event puts at risk at affected points. Banded (not continuous) so the golden
# is robust to a single contract's MDQ being revised by a few Dth.
EXPOSURE_BANDS = (
    (100_000, 1.25),                # (< upper Dth/d) -> factor ; some firm exposure
    (500_000, 1.5),                 # material
    (float("inf"), 2.0),            # large
)
NO_EXPOSURE_FACTOR = 1.0            # no BP contract identified on the risk path

NOVELTY_NEW = 1.0                   # posted since the last brief / no prior brief
NOVELTY_SEEN = 0.5                  # already briefed -> demoted ("unchanged")

CONFIRM_THRESHOLD = 0.6            # a CRITICAL item below this confidence is floored
                                   # into the brief tagged "unconfirmed - verify first"
LOOKAHEAD_DAYS = 7                 # PLANNED events starting within this horizon are briefed

# Which exposed-subject reason leads an item's one-line summary (worst first).
_REASON_PRIORITY = ("contract_at_affected_point", "storage_service_at_risk",
                    "downstream_interconnect", "on_constrained_pipeline",
                    "on_constrained_segment")

_SECTION_TITLE = {
    "critical": "Critical",
    "action": "Action",
    "watch": "Watch",
    "informational": "FYI",
}


def exposure_factor(bp_mdq_dth: int) -> tuple[float, str]:
    """(factor, reason) for BP firm Dth/d at risk. Reason is printed by --explain."""
    if bp_mdq_dth <= 0:
        return NO_EXPOSURE_FACTOR, "no BP firm capacity on the risk path"
    for upper, factor in EXPOSURE_BANDS:
        if bp_mdq_dth < upper:
            return factor, f"{bp_mdq_dth:,} Dth/d BP firm exposed"
    return EXPOSURE_BANDS[-1][1], f"{bp_mdq_dth:,} Dth/d BP firm exposed"


# ------------------------------------------------------------------------------


@dataclass
class ScoreComponents:
    severity_weight: float
    exposure_factor: float
    exposure_reason: str
    novelty: float
    novelty_reason: str
    confidence: float

    @property
    def score(self) -> float:
        return round(self.severity_weight * self.exposure_factor
                     * self.novelty * self.confidence, 3)

    def explain(self, severity: str) -> str:
        nov = "new" if self.novelty == NOVELTY_NEW else "already briefed"
        return (f"score {self.score:.2f} = severity {self.severity_weight:.1f}"
                f" ({severity}) x exposure {self.exposure_factor:.2f}"
                f" ({self.exposure_reason}) x novelty {self.novelty:.1f} ({nov})"
                f" x confidence {self.confidence:.2f}")


@dataclass
class BriefItem:
    event_uid: str
    asset_name: str
    pipe: str
    event_type: str
    severity: str
    status: str
    valid_from: date | None
    valid_to: date | None
    exposure_summary: str
    investigations: list[str]
    components: ScoreComponents
    citations: list[str]
    unconfirmed: bool               # low-confidence critical -> "verify first" tag

    @property
    def score(self) -> float:
        return self.components.score


@dataclass
class DataQualityNote:
    text: str
    citations: list[str]


@dataclass
class Brief:
    as_of: date
    generated_at: datetime
    since: datetime | None
    sections: dict[str, list[BriefItem]]
    data_quality: list[DataQualityNote]
    mode: str                       # "template" | "polished"
    summary: str | None = None      # polished narrative (LLM), gate-verified; None in template mode

    def all_items(self) -> list[BriefItem]:
        # worst first (critical -> informational)
        return [it for sev in reversed(SEVERITY_ORDER)
                for it in self.sections.get(sev, [])]


# ---- assembly ----------------------------------------------------------------


def _pipe_codes(con) -> dict[str, str]:
    return {r[0]: (r[1] or r[0]) for r in con.execute(
        "SELECT ferc_cid, short_code FROM pipeline").fetchall()}


def _impacts_by_event(con) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for r in con.execute("""
        SELECT event_uid, subject_uid, subject_kind, reason_code, hop_distance,
               severity, confidence, investigation, citations
        FROM event_impact ORDER BY event_uid, reason_code, subject_uid
    """).fetchall():
        out.setdefault(r[0], []).append(r)
    return out


def _holding_meta(con) -> dict[str, tuple]:
    return {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT holding_uid, contract_id, mdq_dth, tsp_ferc_cid"
        " FROM contract_holding").fetchall()}


def _headline(impacts, holdings, pipes, seg_cd) -> tuple[str, str, float, int, list, list]:
    """From an event's impact rows: (headline_severity, exposure_summary,
    headline_confidence, bp_mdq_exposed, investigations, citations)."""
    by_reason: dict[str, list] = {}
    cites: set[str] = set()
    for (_e, subj, kind, reason, hop, sev, conf, inv, rcites) in impacts:
        by_reason.setdefault(reason, []).append(
            (subj, kind, hop, sev, conf, inv))
        cites.update(rcites)

    # headline severity = worst hop-0 severity; its confidence = min at that band
    hop0 = [(sev, conf) for rows in by_reason.values()
            for (_s, _k, hop, sev, conf, _i) in rows if hop == 0]
    if hop0:
        hsev = max((s for s, _c in hop0), key=rank)
        hconf = min(c for s, c in hop0 if s == hsev)
    else:                            # no hop-0 row (shouldn't happen); fall back
        hsev, hconf = "informational", 0.0

    parts: list[str] = []
    bp_mdq = 0
    if "contract_at_affected_point" in by_reason:
        names = []
        for (subj, _k, _h, _s, _c, _i) in by_reason["contract_at_affected_point"]:
            cid_k, mdq, tsp = holdings.get(subj, (subj, 0, None))
            bp_mdq += mdq or 0
            names.append(f"{cid_k} ({mdq:,} Dth/d) on {pipes.get(tsp, tsp)}")
        parts.append("BP firm exposed — " + "; ".join(names))
    if "storage_service_at_risk" in by_reason:
        n = len(by_reason["storage_service_at_risk"])
        parts.append(f"{n} storage interconnect{'s' if n > 1 else ''} at risk")
    if "downstream_interconnect" in by_reason:
        dn_pipes = sorted({pipes.get(subj.split(":")[0], subj.split(":")[0])
                           for (subj, _k, _h, _s, _c, _i)
                           in by_reason["downstream_interconnect"]})
        parts.append("downstream: " + ", ".join(dn_pipes))
    if "on_constrained_pipeline" in by_reason:
        parts.append("unmapped — scoped to the whole pipeline system")
    if "on_constrained_segment" in by_reason:
        n = len(by_reason["on_constrained_segment"])
        parts.append(f"{n} point{'s' if n > 1 else ''} on the constrained"
                     f" {seg_cd} segment")
    exposure = "; ".join(parts) if parts else "no downstream exposure identified"

    # up to 2 distinct investigations, worst reason first
    invs: list[str] = []
    for reason in _REASON_PRIORITY:
        for (_subj, _k, _h, _s, _c, inv) in by_reason.get(reason, []):
            if inv not in invs:
                invs.append(inv)
    return hsev, exposure, hconf, bp_mdq, invs[:2], sorted(cites)


def _in_scope(status: str, vf, as_of: date, novel_assembly: bool,
              lookahead: int) -> bool:
    """Assembly rule: brief an event if it is active now, planned within the
    look-ahead, or freshly posted since a PRIOR brief. On the very first brief
    (no prior run) novelty adds nothing — operational relevance (active /
    upcoming) is the whole filter, so the first brief isn't a dump of history."""
    if status == "active":
        return True
    if status == "planned" and vf is not None and vf <= as_of + timedelta(days=lookahead):
        return True
    return novel_assembly


def generate(con, as_of: date | None = None, since: datetime | None = None,
             lookahead_days: int = LOOKAHEAD_DAYS,
             generated_at: datetime | None = None, narrator=None) -> Brief:
    """Assemble → rank → (optionally) polish. Pure read; never mutates the store
    (recording a brief_run is an explicit separate call)."""
    as_of = as_of or date.today()
    generated_at = generated_at or datetime.now()
    if since is None:
        since = last_brief_run(con)          # None on the very first brief

    pipes = _pipe_codes(con)
    impacts = _impacts_by_event(con)
    holdings = _holding_meta(con)

    events = con.execute("""
        SELECT event_uid, tsp_ferc_cid, event_type, asset_name, seg_cd,
               lifecycle_status, valid_from, valid_to,
               (SELECT max(n.post_dt) FROM notice n
                 WHERE list_contains(source_notice_uids, n.notice_uid))
        FROM operational_event
        WHERE lifecycle_status != 'superseded'
        ORDER BY event_uid
    """).fetchall()

    sections: dict[str, list[BriefItem]] = {s: [] for s in SEVERITY_ORDER}
    briefed: list[tuple] = []            # (event row, seg) for data-quality pass

    for (euid, tsp, etype, name, seg, life, vf, vt, max_post) in events:
        status = status_at(vf, vt, life, as_of)
        # ranking novelty: first-ever brief treats everything as new (uniform,
        # no distortion); a later run demotes anything posted before `since`.
        novel = since is None or (max_post is not None and max_post > since)
        # assembly novelty: only a REAL prior brief lets a freshly-posted event
        # in past the active/upcoming filter (see _in_scope).
        novel_assembly = since is not None and max_post is not None and max_post > since
        if not _in_scope(status, vf, as_of, novel_assembly, lookahead_days):
            continue

        hsev, exposure, hconf, bp_mdq, invs, cites = _headline(
            impacts.get(euid, []), holdings, pipes, seg)
        cites = sorted(set(cites) | {f"evt:{euid}"})

        exp_factor, exp_reason = exposure_factor(bp_mdq)
        nov = NOVELTY_NEW if novel else NOVELTY_SEEN
        nov_reason = ("new since last brief" if novel
                      else "already briefed before last run")
        comp = ScoreComponents(
            severity_weight=SEVERITY_WEIGHT[hsev], exposure_factor=exp_factor,
            exposure_reason=exp_reason, novelty=nov, novelty_reason=nov_reason,
            confidence=hconf)
        unconfirmed = hsev == "critical" and hconf < CONFIRM_THRESHOLD

        sections[hsev].append(BriefItem(
            event_uid=euid, asset_name=name, pipe=pipes.get(tsp, tsp),
            event_type=etype, severity=hsev, status=status, valid_from=vf,
            valid_to=vt, exposure_summary=exposure, investigations=invs,
            components=comp, citations=cites, unconfirmed=unconfirmed))
        briefed.append((euid, tsp, etype, name, seg, vf, vt))

    for sev in sections:
        sections[sev].sort(key=lambda it: (-it.score, it.asset_name.lower()))

    data_quality = _data_quality(con, briefed, pipes)

    brief = Brief(as_of=as_of, generated_at=generated_at, since=since,
                  sections=sections, data_quality=data_quality, mode="template")

    if narrator is not None:
        _polish(brief, narrator)
    return brief


# ---- data-quality notes (the section is load-bearing, not decorative) --------


def _data_quality(con, briefed, pipes) -> list[DataQualityNote]:
    notes: list[DataQualityNote] = []

    # 1. Standing cross-EBB staleness: a resolved interconnect whose COUNTERPARTY
    #    point has been retired upstream (the SESH->4208 finding, DoD extra).
    #    Grouped by the retired point, so both SESH points collapse to one note.
    for (b_tsp, b_loc, b_name, a_tsps, a_locs, ic_uids) in con.execute("""
        SELECT ic.b_tsp_ferc_cid, ic.b_loc, any_value(ic.b_name_posted),
               list(DISTINCT ic.a_tsp_ferc_cid),
               list(ic.a_loc ORDER BY ic.a_loc),
               list(ic.interconnect_uid ORDER BY ic.interconnect_uid)
        FROM interconnect ic
        JOIN point p ON p.tsp_ferc_cid = ic.b_tsp_ferc_cid AND p.loc = ic.b_loc
        WHERE p.loc_stat_ind = 'I'
          AND ic.resolution_status IN ('resolved_roundtrip', 'resolved_cid_loc')
        GROUP BY ic.b_tsp_ferc_cid, ic.b_loc
        ORDER BY ic.b_tsp_ferc_cid, ic.b_loc
    """).fetchall():
        a_pipe = "/".join(pipes.get(t, t) for t in a_tsps)
        b_pipe = pipes.get(b_tsp, b_tsp)
        pts = ", ".join(a_locs)
        plural = "points" if len(a_locs) > 1 else "point"
        cites = [f"ic:{u}" for u in ic_uids] + [f"point:{b_tsp}:{b_loc}"]
        notes.append(DataQualityNote(
            f"{a_pipe} {plural} {pts} still declare {b_pipe} point {b_loc}"
            f" ({b_name}), which {b_pipe} has RETIRED — a cross-EBB stale"
            f" reference; confirm the successor point before relying on these"
            f" interconnects.", cites))

    # 2. A force-majeure (or maintenance) event in this brief with NO segment
    #    mapping: impact could only be scoped to the whole pipeline.
    for (euid, tsp, etype, name, seg, _vf, _vt) in briefed:
        if seg is None and etype in ("force_majeure", "maintenance"):
            notes.append(DataQualityNote(
                f"'{name}' ({pipes.get(tsp, tsp)} {etype}) has no segment"
                f" mapping — its impact is scoped to the whole pipeline, not"
                f" specific points; add a segment_asset_map row once the"
                f" affected stations are identified.", [f"evt:{euid}"]))

    # 3. Any briefed event whose extracted figures are all UNVERIFIED.
    for (euid, tsp, etype, name, seg, _vf, _vt) in briefed:
        row = con.execute("""
            SELECT count(*), count(*) FILTER (WHERE verified_by IS NOT NULL)
            FROM capacity_impact_fact
            WHERE notice_uid IN (SELECT unnest(source_notice_uids)
                                 FROM operational_event WHERE event_uid = ?)
        """, [euid]).fetchone()
        if row and row[0] > 0 and row[1] == 0:
            notes.append(DataQualityNote(
                f"'{name}' figures are extraction-only (no desk verification)"
                f" — treat the capacity numbers as leads until confirmed against"
                f" the source notice.", [f"evt:{euid}"]))

    return notes


# ---- optional LLM polish behind the citation gate ----------------------------


def _narration_bundle(brief: Brief) -> tuple[str, set[str]]:
    """The deterministic input bundle a narrator may talk about: the item facts,
    each tagged with the uids it is allowed to cite. Returns (bundle_text,
    valid_uids)."""
    valid: set[str] = set()
    lines = [f"as_of {brief.as_of}"]
    for it in brief.all_items():
        valid.update(it.citations)
        lines.append(f"- [{it.severity}] {it.asset_name} ({it.pipe})"
                     f" {it.event_type}: {it.exposure_summary}"
                     f"  cite-as {' '.join(it.citations)}")
    for note in brief.data_quality:
        valid.update(note.citations)
        lines.append(f"- data-quality: {note.text}"
                     f"  cite-as {' '.join(note.citations)}")
    return "\n".join(lines), valid


def _polish(brief: Brief, narrator) -> None:
    """Run narrator prose through the citation gate; adopt it only if clean."""
    bundle_text, valid = _narration_bundle(brief)
    try:
        prose = narrator(bundle_text, valid)
    except Exception:
        return                       # any narrator failure -> template stands
    result = citegate.verify(prose, valid)
    if result.ok and result.n_kept > 0:
        brief.summary = result.text
        brief.mode = "polished"
    # else: template text stands (fallback), summary remains None


# ---- markdown render ---------------------------------------------------------


def _fmt_window(vf, vt) -> str:
    if vf is None:
        return "window unknown"
    lo = vf.strftime("%b %d")
    hi = vt.strftime("%b %d") if vt else "open"
    return f"{lo} → {hi}"


def render_markdown(brief: Brief, explain: bool = False) -> str:
    w: list[str] = []
    w.append(f"# Morning Brief — {brief.as_of.isoformat()}")
    since = brief.since.isoformat(sep=" ") if brief.since else "no prior brief"
    w.append(f"_Generated {brief.generated_at.isoformat(sep=' ')} · "
             f"novelty since {since} · mode: {brief.mode}_")
    total = len(brief.all_items())
    w.append("")
    w.append(f"**{total} item{'s' if total != 1 else ''}** across the portfolio"
             f" for gas day {brief.as_of.isoformat()}.")

    if brief.summary:
        w.append("")
        w.append("## Summary")
        w.append(brief.summary)

    for sev in reversed(SEVERITY_ORDER):        # Critical → Action → Watch → FYI
        items = brief.sections.get(sev, [])
        if not items:
            continue
        w.append("")
        w.append(f"## {_SECTION_TITLE[sev]}")
        for it in items:
            tag = "  ⚠️ unconfirmed — verify first" if it.unconfirmed else ""
            w.append("")
            w.append(f"### {it.asset_name} — {it.pipe} {it.event_type}{tag}")
            w.append(f"- **When:** {_fmt_window(it.valid_from, it.valid_to)}"
                     f"  ·  status {it.status.upper()} as of {brief.as_of.isoformat()}")
            w.append(f"- **Exposure:** {it.exposure_summary}")
            for inv in it.investigations:
                w.append(f"- **Check:** {inv}")
            if explain:
                w.append(f"- **Rank:** {it.components.explain(it.severity)}")
            w.append(f"- _Citations:_ {' '.join(it.citations)}")

    w.append("")
    w.append("## Data-quality notes")
    if not brief.data_quality:
        w.append("- (none)")
    for note in brief.data_quality:
        w.append(f"- {note.text}  _[{' '.join(note.citations)}]_")

    return "\n".join(w) + "\n"


# ---- CLI ---------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Ranked, cited one-page morning"
                                             " triage brief")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--explain", action="store_true",
                    help="print the ranking score components for each item")
    ap.add_argument("--no-llm", action="store_true",
                    help="template mode only (default); guarantees zero LLM artifacts")
    ap.add_argument("--record", action="store_true",
                    help="record this run in brief_run (advances novelty)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ns = ap.parse_args()

    con = duckdb.connect(ns.db, read_only=not ns.record)
    try:
        as_of = ns.as_of or date.today()
        brief = generate(con, as_of=as_of)      # narrator=None -> template mode
        print(render_markdown(brief, explain=ns.explain))
        if ns.record:
            record_brief_run(con, as_of, as_of, as_of + timedelta(days=LOOKAHEAD_DAYS))
    finally:
        con.close()


if __name__ == "__main__":
    main()
