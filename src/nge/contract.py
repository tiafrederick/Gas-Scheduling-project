"""The wire contract (Era 3, Phase 1) — the transport-agnostic serialization layer.

ONE place decides how the Operation Contract looks as JSON: the shared value objects
(`Citation`, `Confidence`), the response envelope, and per-capability payload
serializers. `render()` (in the capability modules) and `to_dict()` (built here) are
two INDEPENDENT projections of the same reasoning objects — never derive one from the
other. Transports JSON-encode what this module produces and add no shape of their own.

Design authority: `docs/era3-api-boundary.md`. Phase-1 scope (the first Operations
Workspace screen = the Morning Brief) serializes the `brief` payload only; the envelope
and the value objects are already stable, so adding impact/timeline/graph serializers
later is purely additive. Deferred on purpose (era3 §11): cross-rebuild citation
durability, `Claim`/`Fact` value objects (arrive with `impact`), the full error
taxonomy, `describe`.
"""
from __future__ import annotations

import hashlib

CONTRACT_VERSION = 1
DATA_BOUNDARY = "public_ferc_postings"

# Presentation bands — a stable, published vocabulary (DDL §1.3 confidence algebra).
_SOLID, _PROBABLE = 0.9, 0.6

# Section order for the brief (worst-first) — a stable published enum.
SEVERITY_LEVELS = ("critical", "action", "watch", "informational")


def _iso(v):
    """date | datetime | None -> ISO-8601 string | None (both have .isoformat())."""
    return v.isoformat() if v is not None else None


def band(value: float) -> str:
    if value >= _SOLID:
        return "solid"
    if value >= _PROBABLE:
        return "probable"
    return "lead"


# ---- shared value objects ----------------------------------------------------


def citation(ref: str) -> dict:
    """A typed `kind:uid` citation string -> opaque wire object. Clients MUST NOT
    parse `ref` (era3 §8): the kind is a hint, the ref is the whole opaque token."""
    kind = ref.split(":", 1)[0] if ":" in ref else "unknown"
    return {"kind": kind, "ref": ref}


def citations(refs) -> list:
    return [citation(r) for r in refs]


def confidence(value: float, hops: int | None = None) -> dict:
    d = {"value": round(float(value), 4), "band": band(value)}
    if hops is not None:
        d["hops"] = hops
    return d


def fact(fact_uid, metric, low, high, uom, conf, verified) -> dict:
    """An extracted number with provenance — the Era-1 span-gate discipline on the
    wire: no bare quantities, every fact carries uom + confidence + verified + a
    citation (era3 §3.2)."""
    return {"metric": metric, "low": low, "high": high, "uom": uom,
            "confidence": confidence(conf), "verified": bool(verified),
            "citation": citation(f"fact:{fact_uid}")}


# ---- envelope ----------------------------------------------------------------


def envelope(operation: str, as_of, as_known, dataset: dict | None,
             result: dict, warnings=None, error=None) -> dict:
    """The uniform response envelope. Stable within a major `contract_version`;
    capabilities grow inside `result`, never in the envelope."""
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "query": {"as_of": _iso(as_of), "as_known": _iso(as_known)},
        "dataset": dataset or {},
        "result": result,
        "warnings": list(warnings or []),
        "error": error,
    }


def error(code: str, message: str, operation: str, **details) -> dict:
    """Minimal error-as-data shape (era3 §7). `retriable`/rich details are deferred."""
    e = {"code": code, "message": message, "operation": operation}
    if details:
        e["details"] = details
    return e


# ---- dataset / snapshot signature --------------------------------------------

_SNAPSHOT_TABLES = ("pipeline", "point", "interconnect", "operational_event",
                    "event_impact", "contract_holding", "notice",
                    "capacity_impact_fact")


def dataset(con) -> dict:
    """A cheap, honest content signature of the current store build: a hash over core
    table row counts + the latest `system_recorded_at`. Deterministic per build,
    changes when the data changes. The cross-rebuild citation-durability *guarantee*
    is deferred (era3 §9 risk #1); this ships the FIELD so that guarantee is additive
    later, not a schema change."""
    parts = []
    for t in _SNAPSHOT_TABLES:
        try:
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception:
            n = -1
        parts.append(f"{t}:{n}")
    builts = []
    for t in ("operational_event", "notice"):
        try:
            builts.append(con.execute(
                f"SELECT max(system_recorded_at) FROM {t}").fetchone()[0])
        except Exception:
            pass
    built = max([b for b in builts if b], default=None)
    sig = "|".join(parts) + "|" + (built.isoformat() if built else "")
    return {"snapshot_id": "sig-" + hashlib.sha1(sig.encode()).hexdigest()[:16],
            "built_at": built.isoformat() if built else None,
            "boundary": DATA_BOUNDARY}


# ---- capability payload: brief (Phase-1 scope) -------------------------------


def brief_payload(brief) -> dict:
    return {
        "as_of": _iso(brief.as_of),
        "generated_at": _iso(brief.generated_at),
        "since": _iso(brief.since),
        "mode": brief.mode,
        "summary": brief.summary,
        "sections": {sev: [_brief_item(it) for it in brief.sections.get(sev, [])]
                     for sev in SEVERITY_LEVELS},
        "data_quality": [{"statement": n.text, "citations": citations(n.citations)}
                         for n in brief.data_quality],
    }


def _brief_item(it) -> dict:
    c = it.components
    return {
        "event_ref": f"evt:{it.event_uid}",
        "asset_name": it.asset_name,
        "pipe": it.pipe,
        "event_type": it.event_type,
        "severity": it.severity,
        "status": it.status,
        "window": {"from": _iso(it.valid_from), "to": _iso(it.valid_to)},
        "score": it.score,
        "score_components": {            # the ranking arithmetic (numbers)
            "severity_weight": c.severity_weight,
            "exposure_factor": c.exposure_factor,
            "exposure_reason": c.exposure_reason,
            "novelty": c.novelty,
            "novelty_reason": c.novelty_reason,
            "confidence": c.confidence,
        },
        "exposure_summary": it.exposure_summary,
        "investigations": list(it.investigations),
        "unconfirmed": it.unconfirmed,
        "confidence": confidence(c.confidence),   # the graded band (value object)
        "citations": citations(it.citations),
    }


# ---- capability payload: impact (Phase-2) ------------------------------------


def impact_payload(a) -> dict:                # nge.impact.ImpactAssessment
    vf, vt = a.window
    return {
        "subject": {"asset": a.asset,
                    "event": {"ref": f"evt:{a.event_uid}", "name": a.event_name,
                              "type": a.event_type},
                    "pipeline": a.pipeline},
        "window": {"from": _iso(vf), "to": _iso(vt), "status_as_of": a.status_at_as_of},
        "severity": {"level": a.severity, "components": list(a.severity_components)},
        "facts": [fact(*f) for f in a.facts],
        "segment": (_segment(a.segment) if a.segment else None),
        "exposures": [{"reason": reason, "subject": subj, "hop": hop,
                       "severity": sev, "confidence": confidence(conf),
                       "investigation": inv, "citations": citations(cites)}
                      for reason, rows in a.impacts_by_reason.items()
                      for (subj, hop, sev, conf, inv, cites) in rows],
        "confidence": confidence(a.confidence),
        "citations": citations(a.citations),
    }


def _segment(seg) -> dict:                    # (seg_cd, confidence, note)
    seg_cd, conf, note = seg
    return {"seg_cd": seg_cd, "confidence": confidence(conf), "evidence": note}


# ---- capability payload: timeline (Phase-2) ----------------------------------


def timeline_payload(t) -> dict:              # nge.timeline.Timeline
    return {"window": {"from": _iso(t.date_from), "to": _iso(t.date_to)},
            "entries": [_timeline_entry(e) for e in t.entries]}


def _timeline_entry(e) -> dict:
    return {
        "event_ref": f"evt:{e.event_uid}",
        "pipe": e.pipe, "asset_name": e.asset_name, "event_type": e.event_type,
        "seg_cd": e.seg_cd, "lifecycle_status": e.lifecycle_status,
        "window": {"from": _iso(e.valid_from), "to": _iso(e.valid_to)},
        "window_source": e.window_source,
        "confidence": confidence(e.confidence),
        "severity": e.severity,                       # None in as-known views
        "status_as_of": e.status_at_as_of,
        "n_sources": e.n_sources,
        "superseded_by": f"evt:{e.superseded_by_uid}" if e.superseded_by_uid else None,
    }


# ---- capability payload: graph (Phase-2) -------------------------------------
# Graph-edge provenance is a heterogeneous source descriptor (interconnect uid /
# seed ref / catalog path), NOT a typed kind:uid citation — so it is exposed as a
# plain `provenance` string, deliberately distinct from the Citation value object.


def path_payload(origin, destination, paths) -> dict:
    return {"origin": origin, "destination": destination,
            "paths": [{"hops": p.hops, "confidence": confidence(p.confidence),
                       "edges": [_edge(e) for e in p.edges]} for p in paths]}


def _edge(e) -> dict:
    return {"from": e.src, "to": e.dst, "kind": e.kind, "flow": e.flow,
            "flow_wording": e.flow_wording(), "confidence": confidence(e.confidence),
            "provenance": e.citation}


def neighbors_payload(uid, node, edges, labels) -> dict:
    return {
        "point": {"uid": uid, "label": node.label, "kind": node.kind,
                  "active": node.active},
        "neighbors": [{"to": e.dst, "label": labels[e.dst][0],
                       "kind": labels[e.dst][1], "active": labels[e.dst][2],
                       "flow": e.flow, "flow_wording": e.flow_wording(),
                       "confidence": confidence(e.confidence), "via": e.kind,
                       "provenance": e.citation, "lead_only": "lead" in e.note}
                      for e in edges],
    }


def stats_payload(stats) -> dict:
    return {"counts": dict(stats)}
