"""
Model registry: capability tiers and real pricing.

Pricing verified 2026-09-20 from provider pricing pages:
  - Groq:     https://groq.com/pricing  (free tier: ~30 RPM, 14,400 req/day)
  - Gemini:   https://ai.google.dev/gemini-api/docs/pricing (free tier ~1,500 RPD)
  - DeepSeek: https://api-docs.deepseek.com/quick_start/pricing
  - Ollama:   local, $0.00

Prices are USD per 1M tokens.  Re-verify before trusting these for budgeting;
provider pricing moves frequently.

Capability tiers
----------------
Agents declare a *tier*, never a model name.  This keeps model choice a
deployment decision rather than something baked into agent code.

  ``nano``      Classification, field extraction, "which tool should I call".
                Cheapest thing that can follow a schema.
  ``small``     Summarizing tool output, deciding a verdict, deciding whether
                to consult a peer.
  ``standard``  Writing a consultation response with real technical content.
  ``deep``      Principal-engineer work: authoring design alternatives with
                tradeoffs, protocol compatibility reasoning, arguing against
                another agent's proposal. This is where quality actually
                shows up, and it is a small fraction of total calls.

Why this saves money
--------------------
An SME agent's *tools* are deterministic API calls — they cost nothing in
tokens.  The LLM is only invoked for judgment.  A typical ticket is dominated
by a handful of ``deep`` calls plus a larger number of ``nano``/``small`` ones,
so routing the cheap work to an 8B model and reserving the strong model for
design review cuts cost by roughly an order of magnitude versus sending
everything to a frontier model.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

# Tier ordering, weakest to strongest.
TIERS = ("nano", "small", "standard", "deep")

PRICING_AS_OF = "2026-09-20"


@dataclass(frozen=True)
class ModelSpec:
    """One callable model, with the data needed to choose it on cost."""

    id: str                       # provider-native model name
    provider: str                 # matches LLMProvider.name
    tier: str                     # one of TIERS — the strongest tier it serves well
    input_per_mtok: float         # USD per 1M input tokens
    output_per_mtok: float        # USD per 1M output tokens
    cached_input_per_mtok: float = 0.0
    context_window: int = 128_000
    supports_json_mode: bool = True
    # True when covered by a usable free tier (rate-limited but $0)
    has_free_tier: bool = False
    notes: str = ""

    def __post_init__(self):
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")

    @property
    def is_free(self) -> bool:
        return self.input_per_mtok == 0.0 and self.output_per_mtok == 0.0

    def blended_cost_per_mtok(self, output_ratio: float = 0.25) -> float:
        """Cost per 1M tokens assuming ``output_ratio`` of them are output.

        Agent workloads are input-heavy: a long runbook plus ticket context in,
        a few paragraphs out.  The 0.25 default reflects that, and it matters
        for ranking — a model with cheap input and expensive output can look
        better than it is if you rank on input price alone.
        """
        if not 0.0 <= output_ratio <= 1.0:
            raise ValueError("output_ratio must be between 0.0 and 1.0")
        return (
            self.input_per_mtok * (1 - output_ratio)
            + self.output_per_mtok * output_ratio
        )

    def cost_for(self, input_tokens: int, output_tokens: int,
                 cached_input_tokens: int = 0) -> float:
        """Return the USD cost of a call with these token counts."""
        uncached = max(0, input_tokens - cached_input_tokens)
        return (
            uncached * self.input_per_mtok / 1_000_000
            + cached_input_tokens * self.cached_input_per_mtok / 1_000_000
            + output_tokens * self.output_per_mtok / 1_000_000
        )


# ---------------------------------------------------------------------------
# DeepSeek peak/off-peak
# ---------------------------------------------------------------------------

# DeepSeek charges 2x during peak hours: 01:00-04:00 and 06:00-10:00 UTC on
# weekdays. That window is nighttime in the US, so a US team running during
# the workday pays the off-peak rate. We register off-peak prices and expose
# this helper so budgeting can be honest about the swing.
_DEEPSEEK_PEAK_UTC_RANGES = ((1, 4), (6, 10))


def deepseek_is_peak(now: _dt.datetime | None = None) -> bool:
    """Return True if DeepSeek is currently billing at the 2x peak rate."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.weekday() >= 5:  # Saturday/Sunday are always off-peak
        return False
    return any(start <= now.hour < end for start, end in _DEEPSEEK_PEAK_UTC_RANGES)


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

CATALOG: tuple[ModelSpec, ...] = (
    # --- Ollama: local, free, no rate limit. The zero-cost path. ----------
    ModelSpec(
        id="qwen2.5:7b", provider="ollama", tier="nano",
        input_per_mtok=0.0, output_per_mtok=0.0, context_window=32_768,
        has_free_tier=True, notes="Local. Good enough for extraction/routing.",
    ),
    ModelSpec(
        id="qwen2.5:14b", provider="ollama", tier="small",
        input_per_mtok=0.0, output_per_mtok=0.0, context_window=32_768,
        has_free_tier=True, notes="Local. Summarization and verdicts.",
    ),
    ModelSpec(
        id="qwen2.5:32b", provider="ollama", tier="standard",
        input_per_mtok=0.0, output_per_mtok=0.0, context_window=32_768,
        has_free_tier=True, notes="Local. Needs ~20GB RAM.",
    ),
    ModelSpec(
        id="deepseek-r1:32b", provider="ollama", tier="deep",
        input_per_mtok=0.0, output_per_mtok=0.0, context_window=65_536,
        has_free_tier=True,
        notes="Local reasoning model. Slow but free; viable for design review.",
    ),

    # --- Groq: cheapest paid tokens + a real free tier -------------------
    ModelSpec(
        id="llama-3.1-8b-instant", provider="groq", tier="nano",
        input_per_mtok=0.05, output_per_mtok=0.08,
        cached_input_per_mtok=0.025, context_window=128_000,
        has_free_tier=True, notes="840 TPS. Cheapest paid token available.",
    ),
    ModelSpec(
        id="openai/gpt-oss-20b", provider="groq", tier="small",
        input_per_mtok=0.075, output_per_mtok=0.30,
        cached_input_per_mtok=0.0375, context_window=128_000,
        has_free_tier=True, notes="1000 TPS.",
    ),
    ModelSpec(
        id="openai/gpt-oss-120b", provider="groq", tier="standard",
        input_per_mtok=0.15, output_per_mtok=0.60,
        cached_input_per_mtok=0.075, context_window=128_000,
        has_free_tier=True, notes="Strong open-source flagship at 500 TPS.",
    ),
    ModelSpec(
        id="llama-3.3-70b-versatile", provider="groq", tier="deep",
        input_per_mtok=0.59, output_per_mtok=0.79,
        cached_input_per_mtok=0.295, context_window=128_000,
        has_free_tier=True,
        notes="Best quality on Groq, ~GPT-4o level, 394 TPS.",
    ),

    # --- Gemini: free tier covers all of these (~1,500 RPD) --------------
    ModelSpec(
        id="gemini-2.5-flash-lite", provider="gemini", tier="small",
        input_per_mtok=0.10, output_per_mtok=0.40,
        cached_input_per_mtok=0.025, context_window=1_000_000,
        has_free_tier=True, notes="Free tier available. 1M context.",
    ),
    ModelSpec(
        id="gemini-3-flash", provider="gemini", tier="standard",
        input_per_mtok=0.25, output_per_mtok=1.50,
        cached_input_per_mtok=0.025, context_window=1_000_000,
        has_free_tier=True, notes="Free tier available. 1M context.",
    ),

    # --- DeepSeek: off-peak prices; cache hits are absurdly cheap --------
    ModelSpec(
        id="deepseek-flash", provider="deepseek", tier="standard",
        input_per_mtok=0.15, output_per_mtok=0.60,
        cached_input_per_mtok=0.003, context_window=1_000_000,
        notes="Off-peak pricing. Cache hit is 50x cheaper than miss — ideal "
              "for multi-round deliberation where the prefix repeats.",
    ),
    ModelSpec(
        id="deepseek-v4-pro", provider="deepseek", tier="deep",
        input_per_mtok=0.66, output_per_mtok=1.98,
        cached_input_per_mtok=0.022, context_window=1_000_000,
        notes="Off-peak pricing. Frontier-level reasoning for design review.",
    ),
)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def tier_index(tier: str) -> int:
    """Return the ordinal position of ``tier`` in TIERS."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIERS}")
    return TIERS.index(tier)


def models_for_tier(tier: str, allow_stronger: bool = True) -> list[ModelSpec]:
    """Return models that can serve ``tier``, cheapest first.

    A model registered at a stronger tier can always do weaker work, so by
    default a ``nano`` request may be served by a ``deep`` model if that is
    all that is available.  The cost ranking means this only happens when
    nothing cheaper exists.
    """
    want = tier_index(tier)
    candidates = [
        m for m in CATALOG
        if tier_index(m.tier) == want
        or (allow_stronger and tier_index(m.tier) > want)
    ]
    return sorted(
        candidates,
        key=lambda m: (m.blended_cost_per_mtok(), tier_index(m.tier)),
    )


def get_model(model_id: str, provider: str | None = None) -> ModelSpec | None:
    """Look up a model by id, optionally disambiguated by provider."""
    for m in CATALOG:
        if m.id == model_id and (provider is None or m.provider == provider):
            return m
    return None


def free_models() -> list[ModelSpec]:
    """Return every model that costs literally $0.00 per token."""
    return [m for m in CATALOG if m.is_free]


def providers() -> list[str]:
    """Return every distinct provider id in the catalog."""
    seen: list[str] = []
    for m in CATALOG:
        if m.provider not in seen:
            seen.append(m.provider)
    return seen
