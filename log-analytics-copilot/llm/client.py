"""
AgentLLM — the interface agents actually use.

Ties together the router (which model), the provider (how to call it), and the
budget (what it cost).  The contract that matters:

    ``reason()`` never raises and never propagates a provider failure.

It returns ``None`` when no model is configured, the budget is exhausted, or
the provider errored.  Callers treat ``None`` as "use my authored content".
That single rule is what lets the agent network run identically with a
frontier model, with a free local model, or with nothing at all.
"""

from __future__ import annotations

import json
import logging
import re

from .budget import BudgetExceeded, TicketBudget
from .provider import LLMResponse, build_messages, estimate_tokens
from .router import ModelRouter, Resolution, default_router

logger = logging.getLogger(__name__)


class AgentLLM:
    """Per-agent LLM handle: tier-based model selection with cost tracking."""

    def __init__(
        self,
        agent_id: str,
        *,
        tier: str = "standard",
        router: ModelRouter | None = None,
        budget: TicketBudget | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ):
        self.agent_id = agent_id
        self.tier = tier
        self._router = router or default_router()
        self.budget = budget
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._resolution: Resolution | None = None

    # -- model resolution --------------------------------------------------

    @property
    def resolution(self) -> Resolution:
        """The resolved (provider, model), computed once and cached."""
        if self._resolution is None:
            self._resolution = self._router.resolve(self.tier, self.agent_id)
            logger.info("[%s] model: %s", self.agent_id, self._resolution.describe())
        return self._resolution

    @property
    def enabled(self) -> bool:
        """True when a real model is available for this agent."""
        return not self.resolution.is_null

    def describe(self) -> str:
        return self.resolution.describe()

    # -- the one call agents make -----------------------------------------

    def reason(
        self,
        *,
        role_prompt: str,
        stable_context: str = "",
        volatile_context: str = "",
        question: str = "",
        purpose: str = "",
        json_mode: bool = False,
        tier: str | None = None,
    ) -> str | None:
        """Ask the model to reason. Returns ``None`` if unavailable.

        ``tier`` lets one call escalate above the agent's default — an agent on
        ``small`` can request ``deep`` for the single design-review call that
        justifies the cost, without paying for a strong model on every call.
        """
        resolution = (
            self.resolution if tier is None or tier == self.tier
            else self._router.resolve(tier, self.agent_id)
        )
        if resolution.is_null:
            logger.debug(
                "[%s] no model for %r (%s); using authored fallback",
                self.agent_id, purpose or "reason", resolution.reason,
            )
            return None

        messages = build_messages(
            role_prompt=role_prompt,
            stable_context=stable_context,
            volatile_context=volatile_context,
            question=question,
        )

        if self.budget is not None:
            estimate = self._estimate_cost(messages, resolution)
            if self.budget.would_exceed(estimate):
                logger.warning(
                    "[%s] skipping %r: would exceed ticket budget "
                    "(spent $%.4f, est $%.4f, limit $%.4f)",
                    self.agent_id, purpose or "reason",
                    self.budget.total_cost_usd, estimate, self.budget.limit_usd,
                )
                return None

        try:
            response: LLMResponse = resolution.provider.complete(
                messages,
                model=resolution.model_id,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                json_mode=json_mode,
            )
        except Exception as exc:
            # A provider outage must not fail a ticket. Degrade to authored
            # content and keep the deliberation going.
            logger.warning(
                "[%s] LLM call failed for %r (%s); using authored fallback: %s",
                self.agent_id, purpose or "reason", resolution.model_id, exc,
            )
            return None

        if self.budget is not None:
            try:
                self.budget.record(
                    agent_id=self.agent_id,
                    tier=resolution.tier_requested,
                    response=response,
                    purpose=purpose,
                )
            except BudgetExceeded as exc:
                # The call already happened and is already billed, so the text
                # is usable. Log loudly; the ceiling stops the *next* call.
                logger.warning("[%s] budget exceeded: %s", self.agent_id, exc)

        text = (response.text or "").strip()
        return text or None

    def reason_json(self, **kwargs) -> dict | list | None:
        """``reason()`` with JSON parsing. Returns ``None`` on unusable output.

        Small models emit JSON wrapped in prose or fenced code blocks often
        enough that tolerating both is required in practice.
        """
        kwargs.setdefault("json_mode", True)
        text = self.reason(**kwargs)
        if not text:
            return None

        fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Last resort: the outermost {...} or [...] span.
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue

        logger.warning(
            "[%s] could not parse JSON from model output: %.120s",
            self.agent_id, text,
        )
        return None

    # -- helpers -----------------------------------------------------------

    def _estimate_cost(
        self, messages: list[dict[str, str]], resolution: Resolution
    ) -> float:
        """Pre-call cost estimate, for the budget pre-check."""
        if resolution.model is None:
            return 0.0
        input_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
        return resolution.model.cost_for(input_tokens, self.max_tokens)
