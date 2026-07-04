"""Deterministic baseline extractor for CGT-style maintenance/capacity notices.

PURPOSE: an honest accuracy FLOOR, not a general solution. Every pattern here is
tuned to phrasing observed in real CGT postings (see data/fixtures/). It exists so
that (a) the eval harness has something to grade from day one, and (b) the future
LLM extractor has a bar to beat on the growing gold set — if an LLM can't out-score
a page of regexes, it doesn't ship.

Anti-hallucination property: every fact's source_span is the verbatim matched
substring (match.group(0)), so the harness's span gate passes by construction.
A regex can mis-parse, but it cannot invent text that isn't in the notice.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ..models.facts import CapacityImpactFact, Notice, SERVICES

CYCLE_MAP = {"TIMELY": "TIMELY", "TIM": "TIMELY", "EVENING": "EVENING",
             "ID1": "ID1", "ID2": "ID2", "ID3": "ID3"}

# "the backhaul capacity through AlexSEG will be reduced to a level between
#  2,325,000 - 2,450,000 Dth effective Timely Cycle for Gas Day Wednesday, July 8, 2026"
RE_PROSE_REDUCTION = re.compile(
    r"the (?:(backhaul|forwardhaul) )?capacity through (\w+) will be reduced"
    r"(?: to a level)?(?: between| to) ([\d,]+)(?:\s*-\s*([\d,]+))?\s*Dth"
    r" effective (\w+) Cycle for Gas Day \w+, (\w+ \d{1,2}, \d{4})",
    re.IGNORECASE)

# "scheduled for Wednesday, July 8, 2026, through Friday, July 10, 2026"
RE_SCHEDULED_WINDOW = re.compile(
    r"scheduled for \w+, (\w+ \d{1,2}, \d{4}),? through \w+, (\w+ \d{1,2}, \d{4})",
    re.IGNORECASE)

RE_EST_SETTING = re.compile(
    r"Estimated Capacity Setting:\s*([\d,]+)(?:\s*-\s*([\d,]+))?\s*Dth", re.IGNORECASE)
RE_DESIGN_CAP = re.compile(r"Design Capacity:\s*([\d,]+)", re.IGNORECASE)
RE_POSTED_PCT = re.compile(r"Posted Percentage:\s*(\d+)(?:\s*-\s*(\d+))?\s*%", re.IGNORECASE)

# "curtailments to Interruptible, Secondary Firm, and Primary Firm services"
RE_SERVICES = re.compile(r"curtailments to ([^.]+?) services", re.IGNORECASE)

RE_ASSET_IN_SUBJECT = re.compile(r"\(([A-Za-z]+SEG)\)")


def _num(s: Optional[str]) -> Optional[float]:
    return float(s.replace(",", "")) if s else None


def _iso(us_date: str) -> Optional[str]:
    try:
        return datetime.strptime(us_date.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _services(body: str) -> list[str]:
    m = RE_SERVICES.search(body)
    if not m:
        return []
    found = [s for s in SERVICES if s.lower() in m.group(1).lower()]
    return found


def extract(notice: Notice) -> list[CapacityImpactFact]:
    body = notice.body_text or ""
    facts: list[CapacityImpactFact] = []
    seen_metrics: set[tuple[str, str]] = set()

    # Asset + direction come from the richest source available.
    subject_asset = None
    m = RE_ASSET_IN_SUBJECT.search(notice.subject or "")
    if m:
        subject_asset = m.group(1)

    prose = RE_PROSE_REDUCTION.search(body)
    asset = (prose.group(2) if prose else subject_asset) or subject_asset
    direction = (prose.group(1).lower() if prose and prose.group(1) else None)
    if asset is None:
        return []  # nothing attributable — return nothing rather than guess

    window = RE_SCHEDULED_WINDOW.search(body)
    gd_from = _iso(window.group(1)) if window else None
    gd_to = _iso(window.group(2)) if window else None

    common = dict(notice_uid=notice.notice_uid, tsp_ferc_cid=notice.tsp_ferc_cid,
                  asset_name=asset, asset_kind="compressor_segment",
                  direction=direction, extraction_method="regex", confidence=0.9)

    if prose:
        lo, hi = _num(prose.group(3)), _num(prose.group(4)) or _num(prose.group(3))
        facts.append(CapacityImpactFact(
            metric="estimated_capacity_setting", value_low=lo, value_high=hi,
            uom="Dth", valid_gas_day_from=gd_from or _iso(prose.group(6)),
            valid_gas_day_to=gd_to,
            effective_cycle=CYCLE_MAP.get(prose.group(5).upper()),
            affects_services=_services(body), source_span=prose.group(0), **common))
        seen_metrics.add((asset.lower(), "estimated_capacity_setting"))

    m = RE_EST_SETTING.search(body)
    if m and (asset.lower(), "estimated_capacity_setting") not in seen_metrics:
        facts.append(CapacityImpactFact(
            metric="estimated_capacity_setting", value_low=_num(m.group(1)),
            value_high=_num(m.group(2)) or _num(m.group(1)), uom="Dth",
            valid_gas_day_from=gd_from, valid_gas_day_to=gd_to,
            affects_services=_services(body), source_span=m.group(0), **common))

    m = RE_DESIGN_CAP.search(body)
    if m:
        v = _num(m.group(1))
        facts.append(CapacityImpactFact(
            metric="design_capacity", value_low=v, value_high=v, uom="Dth",
            source_span=m.group(0), **common))

    m = RE_POSTED_PCT.search(body)
    if m:
        facts.append(CapacityImpactFact(
            metric="posted_percentage", value_low=float(m.group(1)),
            value_high=float(m.group(2) or m.group(1)), uom="percent",
            source_span=m.group(0), **common))

    return facts
