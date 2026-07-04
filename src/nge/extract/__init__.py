"""Notice-body extraction: pluggable extractors + the eval harness that grades them.

Contract (docs/extraction-schema.md): an extractor is any callable
    extract(notice: Notice) -> list[CapacityImpactFact]
Facts self-validate at construction (controlled vocab, non-empty source_span);
the eval harness additionally enforces the verbatim-span anti-hallucination gate.
"""
from .baseline import extract as baseline_extract

EXTRACTORS = {
    "baseline": baseline_extract,
    # "llm": added in Phase 3+ — same signature, must beat baseline on the gold set.
}

__all__ = ["EXTRACTORS", "baseline_extract"]
