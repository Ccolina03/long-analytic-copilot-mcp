"""
Google Gemini provider.

Worth having despite not being OpenAI-compatible: the free tier is generous
(~1,500 requests/day at $0.00) and the 1M-token context window means a whole
runbook plus a long deliberation history fits without truncation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..provider import LLMProvider, LLMResponse, TokenUsage, env_key, estimate_tokens
from ..registry import get_model

_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    """Gemini via the generativelanguage REST API."""

    name = "gemini"

    def __init__(self, timeout: float = 60.0, free_tier: bool = False):
        self.timeout = timeout
        # When True, cost is reported as $0.00 because the key is on the free
        # tier. Set via SME_LLM_GEMINI_FREE_TIER=1.
        self.free_tier = free_tier
        self.is_free = free_tier

    # -- availability ------------------------------------------------------

    @property
    def api_key(self) -> str | None:
        return env_key("GEMINI_API_KEY", "GOOGLE_API_KEY")

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
            raise RuntimeError("gemini: GEMINI_API_KEY (or GOOGLE_API_KEY) not set")

        # Gemini separates the system instruction from the turn contents.
        system_text = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system"
        )
        contents = [
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [{"text": m.get("content", "")}],
            }
            for m in messages
            if m.get("role") != "system"
        ]

        payload: dict = {
            "contents": contents or [{"role": "user", "parts": [{"text": "Proceed."}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        req = urllib.request.Request(
            f"{_BASE}/models/{model}:generateContent",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"gemini HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"gemini unreachable: {e.reason}") from e

        return self._parse(body, model, messages)

    def _parse(
        self, body: dict, model: str, messages: list[dict[str, str]]
    ) -> LLMResponse:
        text = ""
        for cand in body.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                text += part.get("text", "")

        meta = body.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=int(
                meta.get("promptTokenCount")
                or sum(estimate_tokens(m.get("content", "")) for m in messages)
            ),
            output_tokens=int(meta.get("candidatesTokenCount") or estimate_tokens(text)),
            cached_input_tokens=int(meta.get("cachedContentTokenCount") or 0),
        )

        if self.free_tier:
            cost = 0.0
        else:
            spec = get_model(model, self.name)
            cost = (
                spec.cost_for(usage.input_tokens, usage.output_tokens,
                              usage.cached_input_tokens)
                if spec else 0.0
            )

        return LLMResponse(
            text=text, model=model, provider=self.name,
            usage=usage, cost_usd=cost, raw=body,
        )
