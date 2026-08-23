"""LLM provider abstraction for writing form-filling programs.

Providers: OpenRouter, OpenAI, Anthropic — all HTTP, no SDK dependency.
API keys come from config.jsonc (with ${VAR} expansion) or .env.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class LLMError(Exception):
    pass


class BaseProvider:
    name = "base"

    def __init__(self, api_key: str, model: str, **opts: Any) -> None:
        self.api_key = api_key
        self.model = model
        self.opts = opts

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.opts.get("timeout", 120)) as r:
            return json.loads(r.read().decode())

    def _extract(self, data: dict) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected provider response: {data}") from e


class OpenAIProvider(BaseProvider):
    """openai.com and any OpenAI-compatible endpoint (base_url opt)."""
    name = "openai"

    def complete(self, system: str, user: str) -> str:
        base = self.opts.get("base_url", "https://api.openai.com/v1").rstrip("/")
        data = self._post(
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.opts.get("temperature", 0),
            },
        )
        return self._extract(data)


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def complete(self, system: str, user: str) -> str:
        base = self.opts.get("base_url", "https://openrouter.ai/api/v1").rstrip("/")
        data = self._post(
            f"{base}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": self.opts.get("referer", "https://browserstonks.local"),
                "X-Title": "BrowserStonks",
            },
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.opts.get("temperature", 0),
            },
        )
        return self._extract(data)


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def complete(self, system: str, user: str) -> str:
        base = self.opts.get("base_url", "https://api.anthropic.com").rstrip("/")
        data = self._post(
            f"{base}/v1/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            {
                "model": self.model,
                "max_tokens": self.opts.get("max_tokens", 4096),
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        try:
            return "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected anthropic response: {data}") from e


class OfflineProvider(BaseProvider):
    """No-LLM fallback: emits a generic program from a template."""
    name = "offline"

    def __init__(self, **opts: Any) -> None:
        super().__init__(api_key="", model="offline", **opts)

    def complete(self, system: str, user: str) -> str:
        raise LLMError("offline provider cannot write programs; use rules or planner")


PROVIDERS: dict[str, type[BaseProvider]] = {
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
    "offline": OfflineProvider,
}


def make_provider(cfg: dict[str, Any]) -> BaseProvider:
    """cfg is config.llm: {provider, model, api_key?, ...opts}."""
    name = cfg.get("provider", "offline")
    cls = PROVIDERS.get(name)
    if cls is None:
        raise LLMError(
            f"unknown LLM provider '{name}'. available: {sorted(PROVIDERS)}"
        )
    key = cfg.get("api_key") or os.environ.get(f"{name.upper()}_API_KEY", "")
    if name != "offline" and not key:
        raise LLMError(
            f"no API key for '{name}': set llm.api_key in config.jsonc "
            f"or {name.upper()}_API_KEY in .env"
        )
    opts = {k: v for k, v in cfg.items() if k not in ("provider", "api_key")}
    return cls(api_key=key, **opts)
