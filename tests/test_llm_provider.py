"""Unit tests for byline.llm_provider.

Covers AnthropicProvider, OpenAIProvider, the get_llm_provider factory, and
the lazy-import contract (importing byline.llm_provider must not eagerly
load either SDK).
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from byline.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    LLMProviderError,
    OpenAIProvider,
    get_llm_provider,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _patch_import_module(monkeypatch, name_to_module: dict[str, object]) -> None:
    """Patch ``importlib.import_module`` to return fake modules for specific names.

    The provider classes do ``from importlib import import_module`` inside their
    ``__init__`` methods, so the lookup resolves through ``importlib`` every
    call. Patching the attribute on the ``importlib`` module covers both that
    pattern and any future ``importlib.import_module(...)`` call sites.

    ``name_to_module`` maps SDK module name -> the fake module object (or an
    ``ImportError`` instance to simulate the package being absent).
    Anything not in the mapping falls back to the real ``importlib.import_module``.
    """
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name in name_to_module:
            value = name_to_module[name]
            if isinstance(value, ImportError):
                raise value
            return value
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)


def _build_fake_anthropic(text: str = "hello world") -> tuple[MagicMock, MagicMock]:
    """Construct a fake ``anthropic`` module + the underlying client mock."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=text)]
    fake_response.usage = MagicMock(input_tokens=10, output_tokens=5)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    fake_anthropic = MagicMock()
    fake_anthropic.Anthropic.return_value = fake_client
    return fake_anthropic, fake_client


def _build_fake_openai(text: str = "hi from openai") -> tuple[MagicMock, MagicMock]:
    """Construct a fake ``openai`` module + the underlying client mock."""
    fake_message = MagicMock()
    fake_message.content = text
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = MagicMock(prompt_tokens=12, completion_tokens=7)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    return fake_openai, fake_client


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


def test_anthropic_provider_generate(monkeypatch) -> None:
    """Happy path: generate() returns text and passes args through."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake_anthropic, fake_client = _build_fake_anthropic(text="hello world")
    _patch_import_module(monkeypatch, {"anthropic": fake_anthropic})

    p = AnthropicProvider()
    assert isinstance(p, LLMProvider)
    out = p.generate("sys prompt", "user prompt", max_tokens=100, temperature=0.2)
    assert out == "hello world"

    args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["max_tokens"] == 100
    assert kwargs["temperature"] == 0.2
    assert kwargs["system"] == "sys prompt"
    assert kwargs["messages"] == [{"role": "user", "content": "user prompt"}]


def test_anthropic_provider_uses_custom_model(monkeypatch) -> None:
    """Passing ``model=`` to generate overrides the default."""
    fake_anthropic, fake_client = _build_fake_anthropic()
    _patch_import_module(monkeypatch, {"anthropic": fake_anthropic})

    p = AnthropicProvider(api_key="fake")
    p.generate("s", "u", max_tokens=50, temperature=0.1, model="claude-3-opus")
    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-3-opus"


def test_anthropic_provider_raises_without_package(monkeypatch) -> None:
    """Missing anthropic package -> LLMProviderError on instantiation."""
    _patch_import_module(monkeypatch, {"anthropic": ImportError("no module named anthropic")})
    with pytest.raises(LLMProviderError):
        AnthropicProvider()


def test_anthropic_provider_wraps_call_errors(monkeypatch) -> None:
    """Transport errors inside generate() are re-raised as LLMProviderError."""
    fake_anthropic, fake_client = _build_fake_anthropic()
    fake_client.messages.create.side_effect = RuntimeError("boom")
    _patch_import_module(monkeypatch, {"anthropic": fake_anthropic})

    p = AnthropicProvider(api_key="fake")
    with pytest.raises(LLMProviderError) as excinfo:
        p.generate("s", "u", max_tokens=10, temperature=0.0)
    assert "boom" in str(excinfo.value)


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


def test_openai_provider_generate(monkeypatch) -> None:
    """Happy path: generate() returns text from choices[0].message.content."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    fake_openai, fake_client = _build_fake_openai(text="hi from openai")
    _patch_import_module(monkeypatch, {"openai": fake_openai})

    p = OpenAIProvider()
    assert isinstance(p, LLMProvider)
    out = p.generate("sys", "user", max_tokens=50, temperature=0.1)
    assert out == "hi from openai"

    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["max_tokens"] == 50
    assert kwargs["temperature"] == 0.1
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


def test_openai_provider_base_url_passed(monkeypatch) -> None:
    """OPENAI_BASE_URL gets forwarded to openai.OpenAI(base_url=...)."""
    fake_openai, _ = _build_fake_openai()
    _patch_import_module(monkeypatch, {"openai": fake_openai})

    OpenAIProvider(api_key="fake", base_url="http://localhost:11434/v1")
    _, kwargs = fake_openai.OpenAI.call_args
    assert kwargs["base_url"] == "http://localhost:11434/v1"
    assert kwargs["api_key"] == "fake"


def test_openai_provider_raises_without_package(monkeypatch) -> None:
    """Missing openai package -> LLMProviderError."""
    _patch_import_module(monkeypatch, {"openai": ImportError("no module named openai")})
    with pytest.raises(LLMProviderError):
        OpenAIProvider()


def test_openai_provider_wraps_call_errors(monkeypatch) -> None:
    """Transport errors inside generate() are re-raised as LLMProviderError."""
    fake_openai, fake_client = _build_fake_openai()
    fake_client.chat.completions.create.side_effect = RuntimeError("kaboom")
    _patch_import_module(monkeypatch, {"openai": fake_openai})

    p = OpenAIProvider(api_key="fake")
    with pytest.raises(LLMProviderError) as excinfo:
        p.generate("s", "u", max_tokens=10, temperature=0.0)
    assert "kaboom" in str(excinfo.value)


# ---------------------------------------------------------------------------
# get_llm_provider factory
# ---------------------------------------------------------------------------


def test_get_llm_provider_default_is_anthropic(monkeypatch) -> None:
    """No BYLINE_LLM_PROVIDER + ANTHROPIC_API_KEY -> AnthropicProvider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    fake_anthropic, _ = _build_fake_anthropic()
    _patch_import_module(monkeypatch, {"anthropic": fake_anthropic})

    p = get_llm_provider()
    assert p is not None
    assert p.name == "anthropic"


def test_get_llm_provider_openai(monkeypatch) -> None:
    """BYLINE_LLM_PROVIDER=openai -> OpenAIProvider."""
    monkeypatch.setenv("BYLINE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    fake_openai, _ = _build_fake_openai()
    _patch_import_module(monkeypatch, {"openai": fake_openai})

    p = get_llm_provider()
    assert p is not None
    assert p.name == "openai"


def test_get_llm_provider_openai_forwards_base_url(monkeypatch) -> None:
    """OPENAI_BASE_URL from env makes it to OpenAI(base_url=...)."""
    monkeypatch.setenv("BYLINE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    fake_openai, _ = _build_fake_openai()
    _patch_import_module(monkeypatch, {"openai": fake_openai})

    p = get_llm_provider()
    assert p is not None
    _, kwargs = fake_openai.OpenAI.call_args
    assert kwargs["base_url"] == "http://localhost:8000/v1"


def test_get_llm_provider_anthropic_no_key_returns_none(monkeypatch) -> None:
    """No ANTHROPIC_API_KEY -> None even if SDK is installed."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    assert get_llm_provider() is None


def test_get_llm_provider_openai_no_key_returns_none(monkeypatch) -> None:
    """No OPENAI_API_KEY -> None even with BYLINE_LLM_PROVIDER=openai."""
    monkeypatch.setenv("BYLINE_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_llm_provider() is None


def test_get_llm_provider_unknown_returns_none(monkeypatch, caplog) -> None:
    """Unknown provider name -> None, with a warning logged."""
    import logging

    monkeypatch.setenv("BYLINE_LLM_PROVIDER", "cohere-but-typo")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with caplog.at_level(logging.WARNING, logger="byline.llm_provider"):
        result = get_llm_provider()
    assert result is None
    assert any("unknown" in rec.message.lower() for rec in caplog.records)


def test_get_llm_provider_returns_none_when_sdk_missing(monkeypatch) -> None:
    """Even with a key set, a missing SDK collapses to None gracefully."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    _patch_import_module(monkeypatch, {"anthropic": ImportError("simulated")})
    assert get_llm_provider() is None


# ---------------------------------------------------------------------------
# Lazy import contract
# ---------------------------------------------------------------------------


def test_neither_sdk_loaded_at_module_import() -> None:
    """Importing byline.llm_provider must NOT eagerly load anthropic or openai.

    We launch a fresh Python interpreter so this assertion isn't polluted by
    prior tests in the same process that may have already imported either SDK.
    """
    import subprocess

    code = (
        "import sys, byline.llm_provider; "
        "assert 'anthropic' not in sys.modules, sys.modules.get('anthropic'); "
        "assert 'openai' not in sys.modules, sys.modules.get('openai'); "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout
