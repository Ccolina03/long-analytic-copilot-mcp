"""
Ollama provider — fully free, runs locally, no API key, no rate limit.

This is the zero-cost path.  If you want the agent network to deliberate
without spending anything, install Ollama and pull a model:

    brew install ollama
    ollama serve &
    ollama pull qwen2.5:14b

Tradeoff: local models are slower and weaker than hosted frontier models.
That is acceptable for ``nano``/``small`` tier work (extraction, verdicts,
summarization).  For ``deep`` tier design review the quality gap is real, so
the recommended setup is Ollama for the cheap tiers plus a hosted model for
design review only.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from ..provider import LLMProvider, LLMResponse, TokenUsage, estimate_tokens


class OllamaProvider(LLMProvider):
    """Local Ollama daemon. Free, unlimited, no key."""

    name = "ollama"
    is_free = True

    def __init__(self, host: str = "http://localhost:11434", timeout: float = 300.0):
        self.host = host.rstrip("/")
        self.timeout = timeout

    # -- availability ------------------------------------------------------

    def is_available(self) -> bool:
        """True if the Ollama daemon is listening.

        A raw socket probe rather than an HTTP request: it is fast, and it
        cannot hang on a half-open connection during provider resolution.
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.host)
            host = parsed.hostname or "localhost"
            port = parsed.port or 11434
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (OSError, ValueError):
            return False

    def list_models(self) -> list[str]:
        """Return locally pulled model names, or [] if the daemon is down."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5.0) as resp:
                body = json.loads(resp.read().decode())
            return [m["name"] for m in body.get("models", [])]
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
            return []

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
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"

        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"ollama HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"ollama unreachable at {self.host}: {e.reason}. "
                "Is `ollama serve` running?"
            ) from e

        text = (body.get("message") or {}).get("content") or ""
        usage = TokenUsage(
            input_tokens=int(
                body.get("prompt_eval_count")
                or sum(estimate_tokens(m.get("content", "")) for m in messages)
            ),
            output_tokens=int(body.get("eval_count") or estimate_tokens(text)),
        )

        # Local inference is free by definition.
        return LLMResponse(
            text=text, model=model, provider=self.name,
            usage=usage, cost_usd=0.0, raw=body,
        )
