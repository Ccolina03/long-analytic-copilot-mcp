"""
OpenAI-compatible chat completions provider.

Groq, DeepSeek, OpenRouter, Together, Fireworks, and local vLLM all speak the
same ``/chat/completions`` wire format, so one implementation covers all of
them.  Only the base URL, env var, and provider id differ.

Uses stdlib ``urllib`` rather than ``httpx`` so the runtime has no third-party
dependency — the agents must be able to run in a bare environment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..provider import LLMProvider, LLMResponse, TokenUsage, env_key, estimate_tokens
from ..registry import get_model


class OpenAICompatProvider(LLMProvider):
    """Chat completions against any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key_env: tuple[str, ...],
        timeout: float = 60.0,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env
        self.timeout = timeout

    # -- availability ------------------------------------------------------

    @property
    def api_key(self) -> str | None:
        return env_key(*self._api_key_env)

    def is_available(self) -> bool:
        return self.api_key is not None

    # -- completion --------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        key = self.api_key
        if not key:
            raise RuntimeError(
                f"{self.name}: no API key set (checked {', '.join(self._api_key_env)})"
            )

        payload: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{self.name} HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"{self.name} unreachable: {e.reason}") from e

        return self._parse(body, model, messages)

    # -- response parsing --------------------------------------------------

    def _parse(
        self, body: dict, model: str, messages: list[dict[str, str]]
    ) -> LLMResponse:
        choices = body.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("message") or {}).get("content") or ""

        raw_usage = body.get("usage") or {}
        prompt_tokens = raw_usage.get("prompt_tokens")
        if prompt_tokens is None:
            prompt_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)

        # Cache-hit accounting is reported differently per provider.
        cached = (
            raw_usage.get("prompt_cache_hit_tokens")            # DeepSeek
            or (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens")  # OpenAI
            or 0
        )

        usage = TokenUsage(
            input_tokens=int(prompt_tokens),
            output_tokens=int(raw_usage.get("completion_tokens") or estimate_tokens(text)),
            cached_input_tokens=int(cached),
        )

        spec = get_model(model, self.name) or get_model(model)
        cost = (
            spec.cost_for(usage.input_tokens, usage.output_tokens,
                          usage.cached_input_tokens)
            if spec else 0.0
        )

        return LLMResponse(
            text=text, model=model, provider=self.name,
            usage=usage, cost_usd=cost, raw=body,
        )


# ---------------------------------------------------------------------------
# Preconfigured instances
# ---------------------------------------------------------------------------

def groq() -> OpenAICompatProvider:
    """Groq — cheapest paid tokens, and a free tier (~30 RPM, 14.4k req/day)."""
    return OpenAICompatProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env=("GROQ_API_KEY",),
    )


def deepseek() -> OpenAICompatProvider:
    """DeepSeek — cache hits at $0.003/M make repeated prefixes nearly free."""
    return OpenAICompatProvider(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
    )


def openrouter() -> OpenAICompatProvider:
    """OpenRouter — aggregator; exposes ``:free`` model variants."""
    return OpenAICompatProvider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env=("OPENROUTER_API_KEY",),
    )
