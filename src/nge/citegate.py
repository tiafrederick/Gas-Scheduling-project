"""Citation gate (OI doc §1.2, DDL-014) — the extraction span gate, generalized
to generated prose.

Standalone and LLM-free by construction. Era 1's extraction eval rejects any
fact whose ``source_span`` is not a verbatim substring of its source ("an
extractor may be wrong, but it may not invent text"). Era 2 owes the same
guarantee to *generation*: the moment an LLM narrates, its every sentence must
be traceable to a uid the deterministic core actually produced.

``verify(prose, valid_uids)`` is that check. Given generated text and the set of
uids the narrator was handed (the *input bundle*), it enforces two rules:

  1. **Every sentence carries >= 1 citation.** Uncited sentences are stripped and
     counted (the narrator wandered off its evidence — drop the claim, keep the
     rest).
  2. **Every cited uid exists in the bundle.** A single unknown uid fails the
     WHOLE narration (``ok=False``) — the caller ships template text instead. An
     invented uid means the model is fabricating provenance, which poisons trust
     in all of it, so we don't try to salvage part.

The gate is itself tested with planted violations, the same discipline as
``tests/test_extraction_eval.py::TestScorerCatchesBadExtractions``.

Citation syntax mirrors the typed ``kind:uid`` refs the core already emits
(``evt:``/``fact:``/``point:``/``ic:``/``segmap:``/``holding:``/``pipeline:``):

    Curtailments are possible at 4208R [evt:ab12, imp:9f3c].

Bracket tokens are split on commas only, so a colon-bearing uid like
``point:C000307:4208`` survives intact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# A sentence boundary is terminal punctuation followed by whitespace (or EOF).
# Citations sit inside the sentence, before the terminal mark: "... [uid]."
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CITE_RX = re.compile(r"\[([^\]]+)\]")


@dataclass
class GateResult:
    ok: bool                        # False iff any cited uid was not in the bundle
    text: str                       # kept sentences joined (meaningful only when ok)
    stripped: list[str] = field(default_factory=list)     # uncited sentences removed
    unknown_uids: list[str] = field(default_factory=list)  # cited-but-absent uids
    n_sentences: int = 0
    n_kept: int = 0

    @property
    def n_stripped(self) -> int:
        return len(self.stripped)


def cited_uids(sentence: str) -> list[str]:
    """The uids a sentence cites, in order (comma-split inside every bracket)."""
    out: list[str] = []
    for group in _CITE_RX.findall(sentence):
        out.extend(tok.strip() for tok in group.split(",") if tok.strip())
    return out


def verify(prose: str, valid_uids: Iterable[str]) -> GateResult:
    """Run the citation gate over narrated prose against its input bundle.

    Returns a GateResult. On an unknown uid the result is ``ok=False`` and
    ``text`` is empty — the caller MUST fall back to deterministic template
    output (never ship partially-fabricated provenance).
    """
    valid = set(valid_uids)
    sentences = [s.strip() for s in _SENT_SPLIT.split(prose.strip()) if s.strip()]
    kept: list[str] = []
    stripped: list[str] = []
    unknown: list[str] = []

    for sentence in sentences:
        cites = cited_uids(sentence)
        bad = [u for u in cites if u not in valid]
        if bad:
            unknown.extend(bad)          # collect all, but the gate already failed
            continue
        if not cites:
            stripped.append(sentence)    # no evidence -> drop the claim
            continue
        kept.append(sentence)

    ok = not unknown
    return GateResult(
        ok=ok,
        text=" ".join(kept) if ok else "",
        stripped=stripped,
        unknown_uids=unknown,
        n_sentences=len(sentences),
        n_kept=len(kept),
    )
