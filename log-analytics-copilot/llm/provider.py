"""
LLM provider abstraction.

Design constraints
------------------
1. **Free by default.** With no API keys and no local model, the system runs
   with ``NullLLM`` and every agent falls back to its deterministic authored
   content.  Nothing breaks, nothing costs money.
2. **Cheapest model that can do the job.** Agents declare a capability *tier*
   (see ``llm.registry``), not a model name.  The router resolves the tier to
   the cheapest model actually available in the current environment.
3. **Cost is always accounted for.** Every call returns a ``TokenUsage`` and an
   estimated cost, so a ticket's total spend is measurable.
4. **Cache-friendly prompt order.** ``build_messages()`` puts the stable prefix
   (role, runbook, ticket) first and the volatile part (this round's question)
   last, so providers with prompt caching charge the cache-hit rate on the bulk
   of the tokens across a multi-round deliberation.

Provider implementations live in ``llm/providers/``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Usage + response types
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """Token accounting for one LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0   # subset of input_tokens served from cache

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass
class LLMResponse:
    """Result of one LLM call."""

    text: str
    model: str
    provider: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    # True when this response came from a fallback rather than a real model
    is_fallback: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token estimation (used when a provider doesn't report usage)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token.

    Only used when the provider does not return real usage numbers.  Good
    enough for budget guardrails, not for billing reconciliation.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Prompt construction — cache-friendly ordering
# ---------------------------------------------------------------------------

def build_messages(
    *,
    role_prompt: str,
    stable_context: str = "",
    volatile_context: str = "",
    question: str = "",
) -> list[dict[str, str]]:
    """Build a chat message list ordered for prompt-cache hits.

    Ordering matters: providers that cache prompts (DeepSeek, Anthropic,
    Gemini) match on a *prefix*.  Across a 3-round deliberation the role
    prompt, the agent's runbook, and the ticket text are identical every
    round — only the round's question changes.  Putting the stable material
    first means rounds 2 and 3 hit the cache on the bulk of their input
    tokens, which on DeepSeek is $0.003/M instead of $0.15/M (50x cheaper).

    Args:
        role_prompt:      who this agent is; never changes.
        stable_context:   runbook, ownership, ticket — same every round.
        volatile_context: peer responses and concerns so far — grows per round.
        question:         what we're asking right now.
    """
    system_parts = [role_prompt.strip()]
    if stable_context.strip():
        system_parts.append(stable_context.strip())

    user_parts = []
    if volatile_context.strip():
        user_parts.append(volatile_context.strip())
    if question.strip():
        user_parts.append(question.strip())

    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n\n".join(user_parts) or "Proceed."},
    ]


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Base class for all LLM backends."""

    #: short provider id, e.g. "ollama", "groq", "gemini"
    name: str = "base"

    #: True when this provider costs nothing to call
    is_free: bool = False

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Run a chat completion and return the response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is usable right now.

        Must not raise and must not make a network call that blocks for long —
        checking for an API key or a reachable local socket is the intent.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name} free={self.is_free}>"


# ---------------------------------------------------------------------------
# NullLLM — the zero-cost default
# ---------------------------------------------------------------------------

class NullLLM(LLMProvider):
    """The default when nothing is configured.

    Always reports unavailable, so callers fall back to their deterministic
    authored content.  Costs nothing and never touches the network.
    """

    name = "null"
    is_free = True

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "none",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        return LLMResponse(
            text="",
            model="none",
            provider=self.name,
            is_fallback=True,
        )

    def is_available(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def env_key(*names: str) -> str | None:
    """Return the first non-empty env var among ``names``."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return None
