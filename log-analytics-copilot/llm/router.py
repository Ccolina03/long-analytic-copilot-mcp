"""
Model router: resolve a capability tier to the cheapest available model.

Resolution order for an agent asking for tier T:

1. **Explicit per-agent pin.**  ``SME_LLM_MODEL_<agent_id>=provider/model``
   wins outright. Escape hatch for "the kafka-clients agent must use this model".
2. **Free local first**, if ``SME_LLM_PREFER_FREE`` is set (the default).
   A local Ollama model at $0.00 beats any hosted model on cost.
3. **Cheapest available hosted model** at tier T, ranked on blended cost.
4. **Stronger tier** if nothing is registered at T — a deep model can do nano
   work; it just costs more, so it only wins when nothing cheaper exists.
5. **NullLLM**, meaning the agent falls back to deterministic authored content.

Step 5 is why the system works with no keys at all: routing degrades to "no
model" rather than raising.

Environment variables
---------------------
  ``SME_LLM_MODEL_<agent_id>``   Pin one agent, e.g. ``groq/llama-3.3-70b-versatile``
  ``SME_LLM_TIER_<agent_id>``    Override the tier an agent requests
  ``SME_LLM_PREFER_FREE``        Prefer $0.00 models (default: "1")
  ``SME_LLM_MAX_COST_PER_MTOK``  Refuse models above this blended cost
  ``SME_LLM_DISABLE``            Set to "1" to force NullLLM everywhere

Agent ids contain hyphens (``kafka-clients``); env var names cannot, so hyphens
map to underscores: ``SME_LLM_MODEL_oss_kafka``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .provider import LLMProvider, NullLLM
from .providers import GeminiProvider, OllamaProvider, deepseek, groq, openrouter
from .registry import ModelSpec, get_model, models_for_tier, tier_index


@dataclass
class Resolution:
    """The outcome of resolving a tier request for an agent."""

    provider: LLMProvider
    model: ModelSpec | None
    tier_requested: str
    reason: str

    @property
    def is_null(self) -> bool:
        """True when no model was found and the agent must use its fallback."""
        return self.model is None or isinstance(self.provider, NullLLM)

    @property
    def model_id(self) -> str:
        return self.model.id if self.model else "none"

    def describe(self) -> str:
        if self.is_null:
            return f"no model for tier '{self.tier_requested}' ({self.reason})"
        cost = self.model.blended_cost_per_mtok()
        price = "free" if cost == 0 else f"${cost:.3f}/Mtok blended"
        return (
            f"{self.model.provider}/{self.model.id} "
            f"[tier={self.tier_requested}, {price}] — {self.reason}"
        )


def _env_agent_key(prefix: str, agent_id: str) -> str | None:
    """Read ``<prefix><agent_id>``, tolerating hyphen/underscore differences."""
    for variant in (agent_id.replace("-", "_"), agent_id):
        value = os.environ.get(f"{prefix}{variant}", "").strip()
        if value:
            return value
    return None


class ModelRouter:
    """Resolves tier requests to concrete (provider, model) pairs.

    Provider availability is probed once and cached, since ``is_available()``
    may open a socket (Ollama) and agents resolve models repeatedly during a
    deliberation.
    """

    def __init__(self, providers: dict[str, LLMProvider] | None = None):
        self._providers: dict[str, LLMProvider] = providers if providers is not None \
            else self._default_providers()
        self._availability: dict[str, bool] = {}

    @staticmethod
    def _default_providers() -> dict[str, LLMProvider]:
        return {
            "ollama": OllamaProvider(),
            "groq": groq(),
            "deepseek": deepseek(),
            "openrouter": openrouter(),
            "gemini": GeminiProvider(
                free_tier=os.environ.get("SME_LLM_GEMINI_FREE_TIER", "") == "1"
            ),
        }

    # -- availability ------------------------------------------------------

    def available_providers(self) -> list[str]:
        """Provider ids that are usable right now, availability cached."""
        out = []
        for name, provider in self._providers.items():
            if name not in self._availability:
                try:
                    self._availability[name] = provider.is_available()
                except Exception:
                    self._availability[name] = False
            if self._availability[name]:
                out.append(name)
        return out

    def invalidate_availability(self) -> None:
        """Clear the availability cache (call after changing env vars)."""
        self._availability.clear()

    # -- resolution --------------------------------------------------------

    def resolve(self, tier: str, agent_id: str = "") -> Resolution:
        """Resolve ``tier`` for ``agent_id`` to the cheapest usable model."""
        if os.environ.get("SME_LLM_DISABLE", "") == "1":
            return Resolution(NullLLM(), None, tier, "disabled by SME_LLM_DISABLE")

        # An agent's tier can be overridden without touching its code — useful
        # for "run the whole org on nano to see how much quality we lose".
        if agent_id:
            override = _env_agent_key("SME_LLM_TIER_", agent_id)
            if override:
                tier = override

        tier_index(tier)  # validate early; raises on a typo'd tier

        if agent_id:
            pinned = self._resolve_pin(tier, agent_id)
            if pinned:
                return pinned

        available = set(self.available_providers())
        if not available:
            return Resolution(
                NullLLM(), None, tier,
                "no provider available (set GROQ_API_KEY / GEMINI_API_KEY, "
                "or run `ollama serve` for a free local model)",
            )

        candidates = [m for m in models_for_tier(tier) if m.provider in available]
        if not candidates:
            return Resolution(
                NullLLM(), None, tier,
                f"no model at tier '{tier}' among available providers "
                f"({', '.join(sorted(available))})",
            )

        cap = os.environ.get("SME_LLM_MAX_COST_PER_MTOK", "").strip()
        if cap:
            try:
                limit = float(cap)
                priced_out = [m for m in candidates
                              if m.blended_cost_per_mtok() > limit]
                candidates = [m for m in candidates
                              if m.blended_cost_per_mtok() <= limit]
                if not candidates:
                    return Resolution(
                        NullLLM(), None, tier,
                        f"all {len(priced_out)} candidate(s) exceed "
                        f"SME_LLM_MAX_COST_PER_MTOK=${limit}",
                    )
            except ValueError:
                pass  # malformed cap is ignored rather than fatal

        prefer_free = os.environ.get("SME_LLM_PREFER_FREE", "1") == "1"
        if prefer_free:
            free = [m for m in candidates if m.is_free]
            if free:
                chosen = free[0]
                return Resolution(
                    self._providers[chosen.provider], chosen, tier,
                    "cheapest free model (SME_LLM_PREFER_FREE)",
                )

        chosen = candidates[0]  # models_for_tier() already sorted by cost
        exact = chosen.tier == tier
        return Resolution(
            self._providers[chosen.provider], chosen, tier,
            "cheapest available at requested tier" if exact
            else f"cheapest available; upgraded from '{tier}' to '{chosen.tier}'",
        )

    def _resolve_pin(self, tier: str, agent_id: str) -> Resolution | None:
        """Handle ``SME_LLM_MODEL_<agent>=provider/model``."""
        pin = _env_agent_key("SME_LLM_MODEL_", agent_id)
        if not pin:
            return None

        provider_name, _, model_id = pin.partition("/")
        if not model_id:
            provider_name, model_id = "", pin

        spec = get_model(model_id, provider_name or None)
        if spec is None:
            return Resolution(
                NullLLM(), None, tier,
                f"pinned model {pin!r} is not in the registry",
            )

        provider = self._providers.get(spec.provider)
        if provider is None:
            return Resolution(
                NullLLM(), None, tier, f"no provider for {spec.provider!r}"
            )
        if not provider.is_available():
            return Resolution(
                NullLLM(), None, tier,
                f"pinned {pin!r} but provider {spec.provider!r} is unavailable",
            )

        return Resolution(
            provider, spec, tier, f"pinned via SME_LLM_MODEL_{agent_id}"
        )


#: Process-wide default router.
_default_router: ModelRouter | None = None


def default_router() -> ModelRouter:
    """Return the shared router, creating it on first use."""
    global _default_router
    if _default_router is None:
        _default_router = ModelRouter()
    return _default_router


def reset_default_router() -> None:
    """Drop the shared router. Tests use this after mutating env vars."""
    global _default_router
    _default_router = None
