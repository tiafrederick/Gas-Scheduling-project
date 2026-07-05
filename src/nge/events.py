"""Operational event derivation (OI-1; DDL-016).

Notices are what pipelines post; EVENTS are what schedulers track. This module
deterministically projects the `notice` table into `operational_event` rows:

  * CHAINING — notices about the same physical happening group into one event.
    The chain key is the notice subject's *facility phrase* ("Banner Compressor
    Station", "East Lateral-310 Pigging (4267)") — not the SEG code, because
    two different stations can share a segment (Banner and New Albany are both
    BannSEG) and a scheduler tracks them as separate jobs. Capacity postings,
    which have no facility, key on their full normalized subject.
  * LIFECYCLE — `UPDATE:` extends a chain; `COMPLETED:` closes it and pins the
    end date; `REVISED` creates a NEW event superseding its target (both rows
    kept, linked — the correction history is itself operational signal).
  * WINDOWS — gas-day windows come from extracted facts when available
    (confidence 0.97), else parsed subject dates (0.85), else the notice
    effective date (0.60). The source is recorded, never blended.
  * SEGMENT MAPPING — the '(XxxSEG)' idiom maps through segment_asset_map with
    a referential check against the point catalog's segment codes; assets with
    no mapping get seg_cd NULL — surfaced, never guessed (DDL-013 spirit).

`lifecycle_status` is time-INdependent; the operational status a scheduler
sees (planned/active/completed/superseded) is the pure function status_at().
Derivation is idempotent: rebuilding yields byte-identical rows.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
_MONTH_RX = "|".join(MONTHS)

# Lifecycle prefixes observed in CGT subjects (add per-pipe idioms as data arrives).
_PREFIX_RX = re.compile(r"^\s*(UPDATE:|COMPLETED:|REVISED)\s*", re.IGNORECASE)
_FM_LEAD_RX = re.compile(r"^\s*Force Majeure\s*[:–-]\s*", re.IGNORECASE)

# Facility phrase: the physical thing the notice is about.
_FACILITY_RX = re.compile(
    r"(.+?(?:Compressor Station|Lateral Pipeline|Pigging \(\d+\)))",
    re.IGNORECASE)

_SEG_IDIOM_RX = re.compile(r"\(([A-Za-z]+SEG)\)")

# Date range patterns over the (prefix-stripped) subject.
_RANGE_CROSS_RX = re.compile(          # 'July 8 – August 18, 2026' / 'July 3, 2026, through July 6, 2026'
    rf"({_MONTH_RX}) (\d{{1,2}})(?:, (\d{{4}}))?,?\s*(?:–|—|-|through|to)\s*"
    rf"({_MONTH_RX}) (\d{{1,2}}), (\d{{4}})")
_RANGE_SAME_RX = re.compile(           # 'June 24-25, 2026' / 'July 8-10, 2026'
    rf"({_MONTH_RX}) (\d{{1,2}})\s*[-–]\s*(\d{{1,2}}), (\d{{4}})")
_SINGLE_RX = re.compile(rf"({_MONTH_RX}) (\d{{1,2}}), (\d{{4}})")

_EVENT_TYPE = {
    "maintenance": "maintenance",
    "capacity constraint": "capacity_constraint",
    "force majeure": "force_majeure",
    "rate change": "rate_change",
}

WINDOW_CONFIDENCE = {"fact": 0.97, "subject": 0.85, "effective_date": 0.60}


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def _d(month: str, day: str, year: str) -> date:
    return datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()


def strip_prefixes(subject: str) -> tuple[str, str | None]:
    """Return (stripped subject, lifecycle marker or None)."""
    m = _PREFIX_RX.match(subject)
    marker = None
    if m:
        marker = m.group(1).rstrip(":").upper()      # UPDATE | COMPLETED | REVISED
        subject = subject[m.end():]
    return subject.strip(), marker


def asset_key(subject: str) -> tuple[str, str]:
    """(normalized chain key, human asset name) from a notice subject."""
    stripped, _ = strip_prefixes(subject)
    stripped = _FM_LEAD_RX.sub("", stripped)
    m = _FACILITY_RX.search(stripped)
    if m:
        name = m.group(1).strip()
        return name.lower(), name
    # no facility phrase (capacity postings): key on the whole normalized subject
    return stripped.lower().strip(), stripped.strip()


def parse_window(subject: str) -> tuple[date | None, date | None]:
    """Gas-day window from subject text; (from, to) — either may be None."""
    stripped, _ = strip_prefixes(subject)
    m = _RANGE_CROSS_RX.search(stripped)
    if m:
        m1, d1, y1, m2, d2, y2 = m.groups()
        return _d(m1, d1, y1 or y2), _d(m2, d2, y2)
    m = _RANGE_SAME_RX.search(stripped)
    if m:
        mo, d1, d2, y = m.groups()
        return _d(mo, d1, y), _d(mo, d2, y)
    m = _SINGLE_RX.search(stripped)
    if m:
        mo, d1, y = m.groups()
        one = _d(mo, d1, y)
        # 'Effective June 19, 2026' means an OPEN start, not a one-day window
        if re.search(rf"Effective\s+{mo}\s+{d1}", stripped, re.IGNORECASE):
            return one, None
        return one, one
    return None, None


def status_at(valid_from: date | None, valid_to: date | None,
              lifecycle_status: str, as_of: date) -> str:
    """Operational status a scheduler sees on a given gas day. Pure function."""
    if lifecycle_status == "superseded":
        return "superseded"
    if lifecycle_status == "completed":
        if valid_to and as_of < valid_from:
            return "planned"          # completed notices for a future job: rare, honest
        if valid_to and as_of <= valid_to:
            return "active" if (valid_from is None or as_of >= valid_from) else "planned"
        return "completed"
    if valid_from and as_of < valid_from:
        return "planned"
    if valid_to and as_of > valid_to:
        return "completed"
    return "active"


def derive_events(con) -> int:
    """Project `notice` -> `operational_event`. Idempotent (full rebuild)."""
    con.execute("DELETE FROM operational_event")

    notices = con.execute("""
        SELECT notice_uid, tsp_ferc_cid, notice_id, notice_type, subject,
               effective_dt
        FROM notice ORDER BY post_dt, notice_id
    """).fetchall()

    # segment referential universe + asset->seg mapping (DDL-013)
    seg_universe = {(r[0], r[1]) for r in con.execute(
        "SELECT DISTINCT tsp_ferc_cid, pipeline_seg_cd FROM point"
        " WHERE pipeline_seg_cd IS NOT NULL").fetchall()}
    seg_by_asset = {(r[0], r[1].lower()): r[2] for r in con.execute(
        "SELECT tsp_ferc_cid, asset_name, seg_cd FROM segment_asset_map").fetchall()}

    # fact-derived windows by notice_uid (highest-confidence window source)
    fact_windows = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT notice_uid, min(valid_gas_day_from), max(valid_gas_day_to)"
        " FROM capacity_impact_fact WHERE valid_gas_day_from IS NOT NULL"
        " GROUP BY notice_uid").fetchall()}

    chains: dict[tuple, dict] = {}
    for nuid, tsp, nid, ntype, subject, eff_dt in notices:
        stripped, marker = strip_prefixes(subject)
        key, name = asset_key(subject)
        if marker == "REVISED":
            key, name = key + "#revised", name + " (REVISED)"
        ck = (tsp, key)
        c = chains.setdefault(ck, {
            "tsp": tsp, "key": key, "name": name,
            "event_type": _EVENT_TYPE.get((ntype or "").lower(), "other"),
            "sources": [], "markers": [], "from": None, "to": None,
            "window_source": None, "completed_on": None,
        })
        c["sources"].append(nuid)
        c["markers"].append(marker)

        # window: fact > subject > effective date (per notice; merged over chain)
        if nuid in fact_windows:
            f, t = fact_windows[nuid]
            src = "fact"
        else:
            f, t = parse_window(subject)
            src = "subject" if (f or t) else "effective_date"
            if src == "effective_date" and eff_dt:
                # An effective date marks when the INFORMATION takes effect —
                # never when the work ends. It may open a window, not close one
                # (a dateless FM UPDATE must not shrink an open-ended event).
                f = eff_dt.date() if hasattr(eff_dt, "date") else eff_dt
                t = None
        if f and (c["from"] is None or f < c["from"]):
            c["from"] = f
        if t and (c["to"] is None or t > c["to"]):
            c["to"] = t
        rank = {"fact": 3, "subject": 2, "effective_date": 1}
        if c["window_source"] is None or rank[src] > rank[c["window_source"]]:
            c["window_source"] = src
        if marker == "COMPLETED":
            # a COMPLETED sighting pins the end of the job on its stated gas day
            c["completed_on"] = f or c["completed_on"]

    n = 0
    for (tsp, key), c in sorted(chains.items()):
        markers = set(m for m in c["markers"] if m)
        if "COMPLETED" in markers:
            lifecycle = "completed"
            if c["completed_on"]:
                c["to"] = max(filter(None, [c["to"], c["completed_on"]]),
                              default=c["completed_on"])
        elif "UPDATE" in markers:
            lifecycle = "updated"
        else:
            lifecycle = "posted"

        # supersession: a '#revised' event supersedes its base-key sibling
        supersedes_uid = None
        if key.endswith("#revised"):
            base = key[: -len("#revised")]
            if (tsp, base) in chains:
                supersedes_uid = _uid("evt", tsp, base)

        # seg mapping via the SEG idiom in any source subject (DDL-013),
        # referentially checked against the point catalog's segment codes.
        seg = None
        for nuid in c["sources"]:
            subj = con.execute("SELECT subject FROM notice WHERE notice_uid=?",
                               [nuid]).fetchone()[0]
            m = _SEG_IDIOM_RX.search(subj or "")
            if m:
                seg = seg_by_asset.get((tsp, m.group(1).lower()))
                break
        if seg is not None and (tsp, seg) not in seg_universe:
            raise ValueError(f"segment_asset_map points at unknown segment"
                             f" {seg!r} for {tsp} — mapping/catalog drift")

        con.execute(
            "INSERT INTO operational_event (event_uid, tsp_ferc_cid, event_type,"
            " asset_name, asset_key, seg_cd, lifecycle_status, valid_from,"
            " valid_to, window_source, supersedes_event_uid, source_notice_uids,"
            " confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [_uid("evt", tsp, key), tsp, c["event_type"], c["name"], key, seg,
             lifecycle, c["from"], c["to"], c["window_source"], supersedes_uid,
             c["sources"], WINDOW_CONFIDENCE[c["window_source"]]])
        n += 1

    # mark superseded targets
    con.execute("""
        UPDATE operational_event SET lifecycle_status = 'superseded'
        WHERE event_uid IN (SELECT supersedes_event_uid FROM operational_event
                            WHERE supersedes_event_uid IS NOT NULL)
    """)
    return n
