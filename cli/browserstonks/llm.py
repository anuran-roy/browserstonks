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
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
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


# ---------------------------------------------------- local OpenAI-compatible
# LM Studio / llama.cpp / llmd / Ollama all speak the OpenAI chat-completions
# protocol on localhost. Differences: default ports, model naming, and auth
# (all optional; bearer token when set).
LOCAL_PROVIDERS: dict[str, dict[str, Any]] = {
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234/v1",
        "key_env": "LMSTUDIO_API_KEY",
        # LM Studio reports loaded models via GET /v1/models
        "models_path": "/models",
    },
    "llamacpp": {
        # llama-server default port
        "base_url": "http://127.0.0.1:8080/v1",
        "key_env": "LLAMACPP_API_KEY",
        "models_path": "/models",
    },
    "llmd": {
        "base_url": "http://127.0.0.1:8081/v1",
        "key_env": "LLMD_API_KEY",
        "models_path": "/models",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "key_env": "OLLAMA_API_KEY",
        # ollama lists models at /v1/models too (OpenAI-compat shim)
        "models_path": "/models",
    },
}


class LocalOpenAIProvider(OpenAIProvider):
    """LM Studio / llama.cpp / llmd / Ollama — OpenAI-compatible localhost
    servers. API key optional (bearer when provided; many local servers
    accept any non-empty key or none at all)."""

    def __init__(self, **opts: Any) -> None:
        kind = opts.pop("local_kind", "lmstudio")
        spec = LOCAL_PROVIDERS.get(kind, LOCAL_PROVIDERS["lmstudio"])
        opts.setdefault("base_url", spec["base_url"])
        super().__init__(**opts)
        self.local_kind = kind
        self.local_spec = spec

    def _headers(self) -> dict[str, str]:
        # auth is OPTIONAL for local servers: send bearer only when a key
        # is configured (LM Studio/llama.cpp/llmd ignore it; Ollama checks
        # it only when OLLAMA_API_KEY is set server-side)
        if self.api_key and self.api_key.strip():
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def complete(self, system: str, user: str) -> str:
        base = self.opts.get("base_url", self.local_spec["base_url"]).rstrip("/")
        data = self._post(
            f"{base}/chat/completions",
            self._headers(),
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

    def list_models(self) -> list[str]:
        """Query the server's model list (GET /v1/models)."""
        base = self.opts.get("base_url", self.local_spec["base_url"]).rstrip("/")
        req = urllib.request.Request(base + self.local_spec["models_path"],
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"could not list models from {base}: {e}") from e


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
            return "".join(b.get("text", "") for b in data["content"]) or ""
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
    "lmstudio": LocalOpenAIProvider,
    "llamacpp": LocalOpenAIProvider,
    "llmd": LocalOpenAIProvider,
    "ollama": LocalOpenAIProvider,
    "offline": OfflineProvider,
}

# providers whose API key is optional (local servers)
OPTIONAL_KEY_PROVIDERS = set(LOCAL_PROVIDERS)


def make_provider(cfg: dict[str, Any]) -> BaseProvider:
    """cfg is config.llm: {provider, model, api_key?, ...opts}."""
    name = cfg.get("provider", "offline")
    cls = PROVIDERS.get(name)
    if cls is None:
        raise LLMError(
            f"unknown LLM provider '{name}'. available: {sorted(PROVIDERS)}"
        )
    key = cfg.get("api_key") or os.environ.get(f"{name.upper()}_API_KEY", "")
    if key.startswith("${"):
        key = ""  # unset env var expanded to the literal placeholder
    if not key.strip() and name not in OPTIONAL_KEY_PROVIDERS:
        if name != "offline":
            raise LLMError(
                f"no API key for '{name}': set llm.api_key in config.jsonc "
                f"or {name.upper()}_API_KEY in .env"
            )
        key = ""
    opts = {k: v for k, v in cfg.items() if k not in ("provider", "api_key", "model")}
    if cls is LocalOpenAIProvider:
        opts["local_kind"] = name  # picks port/defaults per provider
    return cls(api_key=key, model=cfg.get("model", ""), **opts)
