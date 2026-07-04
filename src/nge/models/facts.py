"""
Typed fact models for the Knowledge Engine (DDL-003).

Dataclasses now (zero-dependency) — migrate to Pydantic when we add validation
at ingestion boundaries (DDL-011). These mirror schema/canonical.sql. The point
of a typed extraction target is that an LLM (or regex, or a human) fills the SAME
shape, every field carries provenance, and nothing enters the store as a bare
number ripped from prose (risk #2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Controlled vocabularies — keep extraction honest and queryable.
NOTICE_TYPES = ("Maintenance", "Capacity Constraint", "Force Majeure", "Other")
NOTICE_LIFECYCLE = ("Initiate", "Update", "Complete")
CYCLES = ("TIMELY", "EVENING", "ID1", "ID2", "ID3")
DIRECTIONS = ("backhaul", "forwardhaul", "both")
METRICS = ("estimated_capacity_setting", "design_capacity", "posted_percentage",
           "operating_capacity")
SERVICES = ("Primary Firm", "Secondary Firm", "Interruptible")
EXTRACTION_METHODS = ("llm", "regex", "manual")


@dataclass
class Notice:
    """One EBB notice — structured header + unstructured body. Immutable event."""
    tsp_ferc_cid: str
    notice_id: str
    notice_type: str
    notice_stat_desc: str
    critical: bool
    subject: str
    body_text: Optional[str] = None        # populated from source_file in the store;
                                           # gold-label files reference it via source_file
    post_dt: Optional[str] = None          # ISO8601
    effective_dt: Optional[str] = None
    end_dt: Optional[str] = None
    author: Optional[str] = None
    prior_notice_id: Optional[str] = None  # supersession link
    source_file: Optional[str] = None

    @property
    def notice_uid(self) -> str:
        return f"{self.tsp_ferc_cid}:{self.notice_id}"


@dataclass
class CapacityImpactFact:
    """A single quantitative impact extracted from a notice body.

    valid_gas_day_from/to = the real-world window the setting applies to.
    source_span           = the EXACT substring the number came from (auditable).
    confidence + verified_by gate whether downstream reasoning may trust it.
    """
    notice_uid: str
    tsp_ferc_cid: str
    asset_name: str
    asset_kind: str
    metric: str
    value_low: float
    value_high: float
    uom: str
    source_span: str
    direction: Optional[str] = None
    valid_gas_day_from: Optional[str] = None
    valid_gas_day_to: Optional[str] = None
    effective_cycle: Optional[str] = None
    affects_services: list[str] = field(default_factory=list)
    related_point_uids: list[str] = field(default_factory=list)
    extraction_method: str = "manual"
    extraction_model: Optional[str] = None
    confidence: float = 1.0
    verified_by: Optional[str] = None      # human sign-off; None = unverified

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            raise ValueError(f"metric {self.metric!r} not in {METRICS}")
        if self.direction is not None and self.direction not in DIRECTIONS:
            raise ValueError(f"direction {self.direction!r} not in {DIRECTIONS}")
        if self.effective_cycle is not None and self.effective_cycle not in CYCLES:
            raise ValueError(f"cycle {self.effective_cycle!r} not in {CYCLES}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if self.source_span.strip() == "":
            raise ValueError("source_span is required — no unprovenanced facts")
