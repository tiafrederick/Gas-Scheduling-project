"""Natural-language query layer (OI-6.3; DDL-019, OI doc §7).

English in, cited answer out — the original project vision. Two routers map a
question to (intent, params) over the SAME whitelisted registry (nge.intents):

  * **fallback router** — keyword/pattern, no key, deterministic, the CI-tested
    path. Deliberately narrow (the registry's `examples` document supported
    phrasings, §7.8).
  * **LLM router** — a single forced `route` tool-use call; the model MUST pick a
    registered intent or `out_of_scope`. Runs only when a key is present; on any
    refusal/error it degrades to the fallback router (never a guess).

Two confidence channels are reported separately and never blended (§7.6):
routing confidence ("did we understand the question") and answer confidence
(the executor's, per §1.3). An out-of-scope question gets an explicit refusal
that lists what CAN be asked — never a fabricated answer.

Run:  PYTHONPATH=src python3 -m nge.ask "how does the AlexSEG maintenance affect SESH?"
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import date

import duckdb

from .intents import BY_NAME, REGISTRY, ExecResult, capability_list
from .store import DEFAULT_DB

_POINT_UID_RX = re.compile(r"C\d{6}:[A-Za-z0-9]+")
_ISO_DATE_RX = re.compile(r"\d{4}-\d{2}-\d{2}")
OUT_OF_SCOPE = "out_of_scope"


@dataclass
class NLAnswer:
    question: str
    router: str                     # 'llm' | 'fallback'
    intent: str | None              # None / OUT_OF_SCOPE when refused
    params: dict = field(default_factory=dict)
    routing_confidence: float = 0.0
    rendered: str = ""
    citations: list[str] = field(default_factory=list)
    answer_confidence: float = 0.0
    out_of_scope: bool = False

    def render(self) -> str:
        head = f"Q: {self.question}"
        if self.out_of_scope or self.intent in (None, OUT_OF_SCOPE):
            return (f"{head}\n\nThat's outside what this engine can answer with"
                    f" cited, deterministic data (it does not forecast prices,"
                    f" basis, or flows). What I CAN answer:\n{capability_list()}")
        conf_band = ("solid" if self.answer_confidence >= 0.9
                     else "probable — verify" if self.answer_confidence >= 0.6
                     else "lead only — investigate" if self.answer_confidence > 0
                     else "no numeric answer (see note)")
        w = [head,
             f"[router: {self.router} · intent: {self.intent} · routing"
             f" conf {self.routing_confidence:.2f} · answer conf"
             f" {self.answer_confidence:.2f} ({conf_band})]",
             "", self.rendered]
        if self.citations:
            w += ["", f"Citations ({len(self.citations)}): " + " ".join(self.citations)]
        return "\n".join(w)


# ---- fallback (keyword) router ----------------------------------------------


def _pipe_codes(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT short_code FROM pipeline WHERE short_code IS NOT NULL").fetchall()]


def _hub_tokens(con) -> dict[str, str]:
    out = {}
    for hid, name in con.execute("SELECT hub_id, name FROM market_hub").fetchall():
        out[hid.lower()] = hid
        if name:
            out[name.lower()] = hid
    return out


def _match_asset(con, q: str) -> str | None:
    ql = q.lower()
    for (seg,) in con.execute("SELECT DISTINCT asset_name FROM segment_asset_map").fetchall():
        if seg.lower() in ql:
            return seg
    for (name,) in con.execute(
            "SELECT DISTINCT asset_name FROM operational_event").fetchall():
        first = name.split()[0]
        if len(first) > 3 and first.lower() != "capacity" \
                and re.search(rf"\b{re.escape(first.lower())}\b", ql):
            return name
    return None


def _ordered_nodes(con, q: str) -> list[str]:
    """Resolvable graph tokens (point uid | hub | pipe code) in question order."""
    hubs = _hub_tokens(con)
    pipes = {p.lower() for p in _pipe_codes(con)}
    hits: list[tuple[int, str]] = []
    for m in _POINT_UID_RX.finditer(q):
        hits.append((m.start(), m.group()))
    for token in list(hubs) + list(pipes):
        for m in re.finditer(rf"\b{re.escape(token)}\b", q.lower()):
            hits.append((m.start(), hubs.get(token, token.upper())))
    hits.sort()
    seen, out = set(), []
    for _pos, tok in hits:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def route_fallback(con, question: str) -> tuple[str | None, dict, float]:
    """(intent_name | None, params, routing_confidence). None => out of scope."""
    q = question.lower()
    uids = _POINT_UID_RX.findall(question)
    dates = _ISO_DATE_RX.findall(question)
    asset = _match_asset(con, question)

    def has(*words):
        return any(w in q for w in words)

    # storage / capacity stubs are keyword-specific — match them before the
    # general families so "capacity at X" doesn't fall into events_in_window.
    if has("storage", "inventory") or ("balance" in q and not asset):
        fac = next((w for w in ("egan", "bobcat", "sabine hub", "pine prairie")
                    if w in q), "")
        return "storage_status", {"facility": fac.title()}, 0.8
    if "capacity" in q and (uids or " at " in q):
        return "capacity_at_point", {"point": uids[0] if uids else ""}, 0.8

    if has("path", "get from", "route from", "how does gas get") \
            or (" from " in q and " to " in q):
        nodes = _ordered_nodes(con, question)
        if len(nodes) >= 2:
            return "path_between", {"origin": nodes[0], "destination": nodes[1]}, 0.85

    if has("interconnect", "connect", "partner", "counterpart"):
        if uids:
            return "interconnect_partners", {"point": uids[0]}, 0.85
        pipe = next((p for p in _pipe_codes(con) if p.lower() in q), None)
        if pipe:
            return "interconnect_partners", {"pipeline": pipe}, 0.8

    if has("contract", "firm", "exposure", "exposed") and has("bp", "contract", "firm"):
        return "contract_exposure", {"asset": asset or ""}, 0.8

    if has("affect", "impact", "touch", "expose", "assess") and asset:
        return "asset_impact", {"asset": asset}, 0.85

    if has("look up", "what is point", "tell me about", "point") and uids:
        return "point_lookup", {"point": uids[0]}, 0.8

    if dates and has("event", "happening", "scheduled", "timeline", "going on"):
        params = {"date_from": dates[0], "date_to": dates[1] if len(dates) > 1 else dates[0]}
        pipe = next((p for p in _pipe_codes(con) if p.lower() in q), None)
        if pipe:
            params["pipeline"] = pipe
        return "events_in_window", params, 0.85

    # a bare asset mention with no other signal is still most likely an impact ask
    if asset:
        return "asset_impact", {"asset": asset}, 0.6

    return None, {}, 0.0


# ---- LLM router (tool-use) ---------------------------------------------------


def _route_tool() -> dict:
    return {
        "name": "route",
        "description": "Record the single registered intent that answers the"
                       " scheduler's question, plus its parameters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string",
                           "enum": [i.name for i in REGISTRY] + [OUT_OF_SCOPE]},
                "params": {"type": "object",
                           "description": "parameters for the chosen intent"},
                "confidence": {"type": "number",
                               "description": "0-1: how sure you are of the intent"},
            },
            "required": ["intent"],
        },
    }


def _llm_system() -> str:
    lines = ["You translate a natural gas scheduler's question into exactly ONE"
             " registered intent and its parameters, by calling the `route` tool.",
             "You never answer the question yourself and never invent data.",
             "If no intent fits, use intent \"out_of_scope\".", "",
             "Registered intents:"]
    for i in REGISTRY:
        params = ", ".join(i.params_schema.keys())
        lines.append(f"- {i.name}({params}): {i.description}")
        lines.append(f"    e.g. {i.examples[0]}")
    return "\n".join(lines)


def route_llm(client, question: str) -> tuple[str | None, dict, float]:
    resp = client.complete(
        system=_llm_system(),
        messages=[{"role": "user", "content": question}],
        tools=[_route_tool()],
        tool_choice={"type": "tool", "name": "route"},
        max_tokens=512)
    if resp.refused or not resp.tool_calls:
        return None, {}, 0.0
    call = resp.tool_calls[0].input
    intent = call.get("intent")
    params = call.get("params") or {}
    conf = call.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else 0.7
    if intent == OUT_OF_SCOPE or intent not in BY_NAME:
        return OUT_OF_SCOPE if intent == OUT_OF_SCOPE else None, {}, conf
    return intent, params if isinstance(params, dict) else {}, conf


# ---- top-level ask -----------------------------------------------------------


def ask(con, question: str, as_of: date | None = None,
        router: str = "auto") -> NLAnswer:
    as_of = as_of or date.today()
    used, intent, params, routing_conf = None, None, {}, 0.0

    # Try the LLM router when asked for it, or in auto mode when a key is present.
    if router == "llm" or (router == "auto" and _llm_available()):
        try:
            from .llm import LLMClient
            intent, params, routing_conf = route_llm(LLMClient(), question)
            used = "llm"
        except Exception:
            used, intent = None, None           # construction/network failure

    # Keyword fallback when: no LLM path, or (auto mode) the LLM couldn't route.
    if used != "llm" or (intent is None and router == "auto"):
        intent, params, routing_conf = route_fallback(con, question)
        used = "fallback"

    if intent in (None, OUT_OF_SCOPE):
        return NLAnswer(question=question, router=used, intent=intent,
                        routing_confidence=(routing_conf if intent == OUT_OF_SCOPE
                                            else 1.0),
                        out_of_scope=True)

    result: ExecResult = BY_NAME[intent].executor(con, params, as_of)
    return NLAnswer(question=question, router=used, intent=intent, params=params,
                    routing_confidence=routing_conf, rendered=result.text,
                    citations=result.citations,
                    answer_confidence=result.answer_confidence)


def _llm_available() -> bool:
    try:
        from .llm import LLMClient
        return LLMClient.available()
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Ask the scheduling engine in English")
    ap.add_argument("question")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--router", choices=("auto", "llm", "fallback"), default="auto")
    ap.add_argument("--db", default=DEFAULT_DB)
    ns = ap.parse_args()
    con = duckdb.connect(ns.db, read_only=True)
    try:
        print(ask(con, ns.question, ns.as_of, ns.router).render())
    finally:
        con.close()


if __name__ == "__main__":
    main()
