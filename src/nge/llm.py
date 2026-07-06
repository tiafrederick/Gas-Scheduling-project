"""Provider-agnostic LLM client (OI-6.1; DDL-004, OI doc §7.5).

The one place an API key is touched. Everything else in the engine is
deterministic and works with no key (DDL-014) — this module is the optional
shell: NL→intent translation (OI-6) and, later, brief narration (OI-5's polish
seam). Design constraints, all load-bearing:

  * **No SDK types past the boundary.** `complete()` returns a plain
    `LLMResponse` dataclass; callers never import `anthropic`. Swapping
    providers is a change to this file alone.
  * **Imports with no key and no SDK installed.** The `anthropic` import is
    lazy (inside `complete`), so `import nge.llm` always succeeds and the whole
    test suite runs in no-LLM mode. `LLMClient.has_key()` gates the LLM-path
    tests (HAS_KEY pattern).
  * **Model id is configurable** (`NGE_LLM_MODEL`), never hard-coded to one
    model — DDL-004: "model choice will change."
  * **Current-model safe.** No `thinking`, `temperature`, `top_p`, or assistant
    prefill are sent — all of which 400 on Fable 5 / Opus 4.8 / Sonnet 5. A
    `stop_reason == "refusal"` is surfaced as `refused=True` (checked before any
    content read), which the router treats as a routing miss and degrades to the
    keyword fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# The project's configured model (a Claude Fable 5 build); overridable so this
# module stays provider/model-agnostic per DDL-004.
DEFAULT_MODEL = os.environ.get("NGE_LLM_MODEL", "claude-fable-5")
_KEY_ENV = "ANTHROPIC_API_KEY"


class LLMUnavailable(RuntimeError):
    """Raised when a completion is attempted with no key or no SDK installed."""


@dataclass
class ToolCall:
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    refused: bool = False


class LLMClient:
    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL

    @staticmethod
    def has_key() -> bool:
        return bool(os.environ.get(_KEY_ENV))

    @staticmethod
    def sdk_installed() -> bool:
        import importlib.util
        return importlib.util.find_spec("anthropic") is not None

    @classmethod
    def available(cls) -> bool:
        return cls.has_key() and cls.sdk_installed()

    def complete(self, system: str, messages: list[dict], tools=None,
                 tool_choice=None, max_tokens: int = 1024) -> LLMResponse:
        """One completion. `messages` and `tools` use the plain Messages-API
        JSON shapes (dicts) — no provider objects. Returns an LLMResponse."""
        if not self.has_key():
            raise LLMUnavailable(f"{_KEY_ENV} not set")
        try:
            import anthropic
        except ImportError as e:                       # pragma: no cover
            raise LLMUnavailable("anthropic SDK not installed") from e

        client = anthropic.Anthropic()
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=messages)
        # No thinking / temperature / top_p: those 400 on current models.
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        try:
            msg = client.messages.create(**kwargs)
        except anthropic.APIError as e:                # network/rate/4xx/5xx
            raise LLMUnavailable(str(e)) from e

        stop = getattr(msg, "stop_reason", "") or ""
        if stop == "refusal":
            return LLMResponse(text="", stop_reason=stop, refused=True)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(name=block.name, input=dict(block.input)))
        return LLMResponse(text="".join(text_parts), tool_calls=calls,
                           stop_reason=stop)
