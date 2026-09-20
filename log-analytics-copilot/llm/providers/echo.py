"""
Deterministic test providers. No network, no cost.

``ScriptedProvider`` is how the whole agent network is tested end to end without
an API key.  It lets a test assert on *how the agent uses the model* — the
prompts it builds, the order of calls, how it handles a refusal — which is the
part that actually needs testing.  Whether a real model writes good prose is not
something a unit test can assert anyway.
"""

from __future__ import annotations

from ..provider import LLMProvider, LLMResponse, TokenUsage, estimate_tokens


class ScriptedProvider(LLMProvider):
    """Returns queued responses in order; records every call for assertions."""

    name = "scripted"
    is_free = True

    def __init__(self, responses: list[str] | None = None, *, default: str = "OK"):
        self._responses = list(responses or [])
        self._default = default
        #: every (messages, model, kwargs) triple this provider was called with
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return True

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "scripted-model",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "json_mode": json_mode,
        })

        text = self._responses.pop(0) if self._responses else self._default
        input_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)

        return LLMResponse(
            text=text, model=model, provider=self.name,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=estimate_tokens(text),
            ),
            cost_usd=0.0,
        )

    # -- assertion helpers -------------------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_prompt(self) -> str:
        """Concatenated content of the most recent call's messages."""
        if not self.calls:
            raise AssertionError("provider was never called")
        return "\n".join(m.get("content", "") for m in self.calls[-1]["messages"])

    def system_prompt(self, index: int = -1) -> str:
        """The system message of the call at ``index``."""
        if not self.calls:
            raise AssertionError("provider was never called")
        return "\n".join(
            m.get("content", "")
            for m in self.calls[index]["messages"]
            if m.get("role") == "system"
        )


class FailingProvider(LLMProvider):
    """Always raises. Proves agents degrade to authored content on failure."""

    name = "failing"
    is_free = True

    def __init__(self, message: str = "simulated provider outage"):
        self.message = message
        self.attempts = 0

    def is_available(self) -> bool:
        return True

    def complete(self, messages, *, model="failing-model", **kwargs) -> LLMResponse:
        self.attempts += 1
        raise RuntimeError(self.message)
