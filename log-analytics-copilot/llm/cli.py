"""
Inspect model routing and cost without running a ticket.

    python -m llm.cli                 # what each agent would use right now
    python -m llm.cli --catalog       # the full model catalog with prices
    python -m llm.cli --estimate      # projected cost per ticket and per 1k

Answers the two questions you actually have when configuring this: "which
model is each agent going to use given my keys?" and "what will that cost?"
"""

from __future__ import annotations

import argparse
import os

from .registry import CATALOG, PRICING_AS_OF, TIERS, deepseek_is_peak
from .router import default_router

AGENTS = (
    ("mirrormaker", "standard",
     "MirrorMaker 2 — owns tickets, writes the 1-pager"),
    ("group-coordinator", "small",
     "Consumer groups — memory math, rebalance history"),
    ("kafka-broker", "small",
     "Broker routing — fan-out cost, heap budget, KRaft metadata"),
    ("kafka-clients", "deep",
     "Kafka wire protocol — compatibility, AdminClient, KIP process"),
    ("kafka-security", "small",
     "Authorization — ACLs, information disclosure, authorizer cost"),
)


def show_providers() -> list[str]:
    router = default_router()
    available = router.available_providers()

    print("Providers")
    print("-" * 72)
    for name in sorted(router._providers):
        if name in available:
            print(f"  {name:<12} available")
        else:
            hint = {
                "ollama": "run `ollama serve` (free, local)",
                "groq": "set GROQ_API_KEY (free tier: ~14,400 req/day)",
                "gemini": "set GEMINI_API_KEY (free tier: ~1,500 req/day)",
                "deepseek": "set DEEPSEEK_API_KEY",
                "openrouter": "set OPENROUTER_API_KEY",
            }.get(name, "not configured")
            print(f"  {name:<12} unavailable — {hint}")
    print()
    return available


def show_agent_routing() -> None:
    router = default_router()
    print("Agent model assignments")
    print("-" * 72)
    for agent_id, tier, description in AGENTS:
        resolution = router.resolve(tier, agent_id)
        print(f"  {agent_id}")
        print(f"    {description}")
        print(f"    -> {resolution.describe()}")
    print()


def show_catalog() -> None:
    print(f"Model catalog (pricing as of {PRICING_AS_OF}, USD per 1M tokens)")
    print("-" * 72)
    print(f"  {'model':<30} {'provider':<11} {'tier':<9} {'in':>7} {'out':>7} {'blend':>7}")
    for tier in TIERS:
        for m in sorted(CATALOG, key=lambda x: x.blended_cost_per_mtok()):
            if m.tier != tier:
                continue
            blend = m.blended_cost_per_mtok()
            label = "free" if m.is_free else f"{blend:.3f}"
            print(
                f"  {m.id:<30} {m.provider:<11} {m.tier:<9} "
                f"{m.input_per_mtok:>7.3f} {m.output_per_mtok:>7.3f} {label:>7}"
            )
    print()
    if deepseek_is_peak():
        print("  Note: DeepSeek is currently in PEAK hours — 2x the listed price.")
    else:
        print("  Note: DeepSeek is currently OFF-PEAK — listed prices apply.")
    print()


def show_estimate() -> None:
    """Project per-ticket cost from the network's real call pattern.

    A 3-agent, 3-round deliberation makes ~6 enrichment calls. Prompts run
    roughly 4k tokens (role + runbook + ticket + alternatives) with ~700
    tokens out.
    """
    calls, tokens_in, tokens_out = 6, 4000, 700
    router = default_router()

    print(f"Cost estimate: {calls} calls/ticket, {tokens_in} in / {tokens_out} out each")
    print("-" * 72)

    total = 0.0
    for agent_id, tier, _ in AGENTS:
        resolution = router.resolve(tier, agent_id)
        if resolution.is_null:
            print(f"  {agent_id:<16} no model — authored fallback, $0.0000")
            continue
        per_call = resolution.model.cost_for(tokens_in, tokens_out)
        agent_total = per_call * (calls / len(AGENTS))
        total += agent_total
        print(
            f"  {agent_id:<16} {resolution.model.provider}/{resolution.model.id:<28} "
            f"${agent_total:.5f}"
        )

    print("-" * 72)
    print(f"  {'per ticket':<16} ${total:.5f}")
    print(f"  {'per 1,000':<16} ${total * 1000:.2f}")
    if total == 0.0:
        print("\n  Running entirely free (local models or no model configured).")
    print()

    print("Cheaper still:")
    print("  - ollama serve + `ollama pull qwen2.5:14b`   -> $0.00, fully local")
    print("  - export GROQ_API_KEY=...                    -> free tier, hosted")
    print("  - export SME_LLM_MAX_COST_PER_MTOK=0.30      -> hard cost ceiling")
    print("  - export SME_LLM_TIER_kafka_clients=small   -> downgrade one agent")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="store_true",
                        help="print the model catalog with prices")
    parser.add_argument("--estimate", action="store_true",
                        help="project per-ticket cost")
    args = parser.parse_args()

    if args.catalog:
        show_catalog()
        return

    available = show_providers()
    show_agent_routing()

    if args.estimate:
        show_estimate()

    if not available:
        print("No provider configured. The network still runs — every agent")
        print("falls back to deterministic authored content at $0.00.")
        print()
        print("For a free local model:  brew install ollama && ollama serve")
        print("                         ollama pull qwen2.5:14b")
        print("For a free hosted tier:  export GROQ_API_KEY=...")

    overrides = {k: v for k, v in os.environ.items() if k.startswith("SME_LLM_")}
    if overrides:
        print("Active overrides")
        print("-" * 72)
        for key, value in sorted(overrides.items()):
            print(f"  {key}={value}")


if __name__ == "__main__":
    main()
