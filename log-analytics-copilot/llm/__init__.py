"""
LLM layer for the SME agent network.

Cost model
----------
Agents declare a capability *tier*, not a model.  The router resolves the tier
to the cheapest model available in the current environment, preferring free
ones.  With nothing configured the system runs at $0.00 on deterministic
authored content.

Quick start — completely free, local:

    brew install ollama && ollama serve &
    ollama pull qwen2.5:14b
    ollama pull deepseek-r1:32b        # for deep-tier design review

Quick start — free hosted tier:

    export GROQ_API_KEY=...            # ~14,400 requests/day free
    export GEMINI_API_KEY=... && export SME_LLM_GEMINI_FREE_TIER=1

Pin one agent to a stronger model where quality actually pays off:

    export SME_LLM_MODEL_kafka_clients=groq/llama-3.3-70b-versatile
"""

from .budget import BudgetExceeded, BudgetLedger, CallRecord, TicketBudget
from .client import AgentLLM
from .provider import (
    LLMProvider,
    LLMResponse,
    NullLLM,
    TokenUsage,
    build_messages,
    estimate_tokens,
)
from .registry import (
    CATALOG,
    PRICING_AS_OF,
    TIERS,
    ModelSpec,
    deepseek_is_peak,
    free_models,
    get_model,
    models_for_tier,
)
from .router import ModelRouter, Resolution, default_router, reset_default_router

__all__ = [
    "AgentLLM",
    "BudgetExceeded",
    "BudgetLedger",
    "CATALOG",
    "CallRecord",
    "LLMProvider",
    "LLMResponse",
    "ModelRouter",
    "ModelSpec",
    "NullLLM",
    "PRICING_AS_OF",
    "Resolution",
    "TIERS",
    "TicketBudget",
    "TokenUsage",
    "build_messages",
    "deepseek_is_peak",
    "default_router",
    "estimate_tokens",
    "free_models",
    "get_model",
    "models_for_tier",
    "reset_default_router",
]
