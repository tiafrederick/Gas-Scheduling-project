"""Severity model for operational events (OI-3.1; DDL-017).

One constants block, pure functions, no I/O — every threshold in this file is
an explicit, reviewable hypothesis about how a scheduler triages. Change a
constant and the golden tests show you exactly what re-ranks.

severity = max(quantitative band, qualitative floor), then:
  * UNVERIFIED cap — severity driven purely by unverified extraction facts
    cannot exceed 'action'; 'critical' requires the event TYPE itself
    (force majeure / OFO) or a human-verified fact. A hallucinated Dth value
    must never be able to scream CRITICAL on its own.
  * hop decay — severity steps down one band per hop away from the constrained
    asset (an 'action' at the segment is a 'watch' one interconnect away).

The quantitative band uses the CONSERVATIVE end of range facts (max cut):
a scheduler plans for the bad end of "2,325,000 - 2,450,000 Dth".
"""
from __future__ import annotations

# ---- the constants block (reviewable hypotheses) ----------------------------

SEVERITY_ORDER = ("informational", "watch", "action", "critical")

# cut% = 1 - setting/design; band = first threshold the cut falls under
QUANT_BANDS = (
    (0.05, "informational"),   # < 5%   routine operational noise
    (0.15, "watch"),           # 5-15%  meaningful squeeze
    (1.01, "action"),          # > 15%  expect scheduling consequences
)

# a notice naming curtailable service classes floors severity qualitatively
SERVICE_FLOORS = {
    "Interruptible": "informational",
    "Secondary Firm": "watch",
    "Primary Firm": "action",       # firm service named => act, whatever the math says
}

# the event type itself can floor severity regardless of numbers
EVENT_TYPE_FLOORS = {
    "force_majeure": "critical",
    "ofo": "critical",              # reserved; no OFO in corpus yet
}

# a named station/lateral job on a segment you have mapped is always worth
# watching, even before any Dth numbers are extracted — that IS desk practice
MAINTENANCE_MAPPED_FLOOR = "watch"

HOP_DECAY_BANDS = 1                 # severity bands lost per hop of distance

# ------------------------------------------------------------------------------


def rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)


def max_severity(*severities: str) -> str:
    return max(severities, key=rank)


def quant_band(cut_pct: float) -> str:
    """Band for a fractional capacity cut (0.134 => 'watch')."""
    for threshold, band in QUANT_BANDS:
        if cut_pct < threshold:
            return band
    return QUANT_BANDS[-1][1]


def decay(severity: str, hops: int) -> str:
    """Step severity down HOP_DECAY_BANDS per hop, floored at informational."""
    idx = max(0, rank(severity) - hops * HOP_DECAY_BANDS)
    return SEVERITY_ORDER[idx]


def compute(event_type: str,
            cut_pct: float | None,
            affected_services: list[str],
            seg_mapped: bool,
            facts_verified: bool) -> tuple[str, list[str]]:
    """Base (hop-0) severity + an explainable list of scoring components.

    Returns (severity, components) where components narrate every rule that
    fired — the '--explain' contract: no score without its reasons.
    """
    components: list[str] = []
    candidates = ["informational"]

    if cut_pct is not None:
        band = quant_band(cut_pct)
        candidates.append(band)
        components.append(f"quantitative: {cut_pct:.1%} capacity cut => {band}"
                          f" (conservative end of range)")

    for svc in affected_services:
        floor = SERVICE_FLOORS.get(svc)
        if floor:
            candidates.append(floor)
            components.append(f"qualitative: notice names '{svc}' => >= {floor}")

    type_floor = EVENT_TYPE_FLOORS.get(event_type)
    if type_floor:
        candidates.append(type_floor)
        components.append(f"event type '{event_type}' => {type_floor}")

    if event_type == "maintenance" and seg_mapped and cut_pct is None:
        candidates.append(MAINTENANCE_MAPPED_FLOOR)
        components.append(f"named job on a mapped segment, no numbers yet =>"
                          f" >= {MAINTENANCE_MAPPED_FLOOR}")

    severity = max_severity(*candidates)

    # UNVERIFIED cap: unverified extraction facts alone may not reach critical
    if severity == "critical" and type_floor != "critical" and not facts_verified:
        severity = "action"
        components.append("UNVERIFIED cap: critical requires a verified fact"
                          " or an FM/OFO event type => capped at action")

    if not components:
        components.append("no quantitative data, no qualitative floors =>"
                          " informational")
    return severity, components
