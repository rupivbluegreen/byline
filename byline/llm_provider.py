"""Provider abstraction for LLM backends.

byline talks to Claude, OpenAI's API, and any OpenAI-compatible
self-hosted endpoint (Ollama, vLLM, LM Studio, llama.cpp) through one
small interface. Concrete provider selection is driven by env vars,
not by any caller. The framing rules and banned-phrase scrubbing in
byline.llm apply to every provider's output uniformly.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class LLMProviderError(RuntimeError):
    """Raised when a provider cannot be constructed or a call fails irrecoverably."""


class LLMProvider(ABC):
    """Common surface for chat completions."""

    name: str
    default_model: str

    @abstractmethod
    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> str:
        """Single-turn generation. Returns the text reply. Raises
        LLMProviderError on transport or response failure."""


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-5"

    def __init__(self, *, api_key: str | None = None) -> None:
        from importlib import import_module
        try:
            anthropic = import_module("anthropic")
        except ImportError as exc:
            raise LLMProviderError(
                "anthropic package not installed; pip install 'byline-audit[llm]'"
            ) from exc
        self._anthropic = anthropic
        # If api_key is None, the SDK reads ANTHROPIC_API_KEY from env.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def generate(self, system, user, *, max_tokens, temperature, model=None):
        try:
            response = self._client.messages.create(
                model=model or self.default_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            logger.debug(
                "anthropic call: input=%s output=%s",
                getattr(response.usage, "input_tokens", None),
                getattr(response.usage, "output_tokens", None),
            )
            return text
        except Exception as exc:
            raise LLMProviderError(f"Anthropic call failed: {exc}") from exc


class OpenAIProvider(LLMProvider):
    """OpenAI (or any OpenAI-compatible endpoint) via the openai SDK.

    Set OPENAI_BASE_URL to redirect to a self-hosted endpoint:
        export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama
        export OPENAI_BASE_URL=http://localhost:8000/v1    # vLLM, LM Studio
    """

    name = "openai"
    default_model = "gpt-4o"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        from importlib import import_module
        try:
            openai = import_module("openai")
        except ImportError as exc:
            raise LLMProviderError(
                "openai package not installed; pip install 'byline-audit[llm]'"
            ) from exc
        self._openai = openai
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def generate(self, system, user, *, max_tokens, temperature, model=None):
        try:
            response = self._client.chat.completions.create(
                model=model or self.default_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage is not None:
                logger.debug(
                    "openai call: input=%s output=%s",
                    getattr(usage, "prompt_tokens", None),
                    getattr(usage, "completion_tokens", None),
                )
            return text
        except Exception as exc:
            raise LLMProviderError(f"OpenAI call failed: {exc}") from exc


def get_llm_provider() -> LLMProvider | None:
    """Resolve a provider from env. Returns None if no usable provider
    is configured (missing key or SDK not installed for the chosen
    provider). Defaults to anthropic when BYLINE_LLM_PROVIDER is unset.
    """
    provider_name = (os.environ.get("BYLINE_LLM_PROVIDER") or "anthropic").lower().strip()

    if provider_name == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            logger.info("ANTHROPIC_API_KEY not set; no LLM provider available")
            return None
        try:
            return AnthropicProvider()
        except LLMProviderError as exc:
            logger.warning("AnthropicProvider unavailable: %s", exc)
            return None

    if provider_name == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            logger.info("OPENAI_API_KEY not set; no LLM provider available")
            return None
        base_url = os.environ.get("OPENAI_BASE_URL")
        try:
            return OpenAIProvider(base_url=base_url)
        except LLMProviderError as exc:
            logger.warning("OpenAIProvider unavailable: %s", exc)
            return None

    logger.warning(
        "Unknown BYLINE_LLM_PROVIDER=%r (expected 'anthropic' or 'openai'); no provider",
        provider_name,
    )
    return None
