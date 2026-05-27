"""Tests for v0.2 LLM additions: prompts, post-processing, and helpers (§7).

v0.3 update: callers now pass an ``LLMProvider`` (see
``byline.llm_provider``) rather than a raw anthropic client. Tests mock the
provider's ``generate`` method directly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from byline.llm import (
    ALIGNMENT_SEMANTIC_SYSTEM_PROMPT,
    ALIGNMENT_SUMMARY_PROMPT,
    BANNED_PHRASES,
    CHAT_SYSTEM_PROMPT,
    QUESTIONS_SYSTEM_PROMPT,
    LLMResponseError,
    LLMUnavailableError,
    get_anthropic_client,
    run_alignment_semantic,
    run_chat_turn,
    run_questions,
    strip_banned_phrases,
)
from byline.llm_provider import LLMProvider

# ---------------------------------------------------------------------------
# strip_banned_phrases
# ---------------------------------------------------------------------------


def test_strip_banned_phrases_basic():
    text = "This is not an AI detector tool"
    result = strip_banned_phrases(text)
    assert "AI detector" not in result
    assert "[redacted]" in result


def test_strip_banned_phrases_case_insensitive_lower():
    result = strip_banned_phrases("avoid ai detector usage")
    assert "ai detector" not in result.lower() or "[redacted]" in result
    assert "[redacted]" in result


def test_strip_banned_phrases_case_insensitive_mixed():
    result = strip_banned_phrases("This is an AI Detector")
    assert "[redacted]" in result
    assert "AI Detector" not in result


def test_strip_banned_phrases_detect_ai():
    result = strip_banned_phrases("We do not detect AI here")
    assert "detect AI" not in result
    assert "[redacted]" in result


def test_strip_banned_phrases_candidate_used_ai():
    result = strip_banned_phrases("Conclusion: the candidate used AI extensively")
    assert "the candidate used AI" not in result
    assert "[redacted]" in result


def test_strip_banned_phrases_no_match_unchanged():
    text = "Plain prose with no banned terms."
    assert strip_banned_phrases(text) == text


def test_banned_phrases_list_present():
    assert "AI detector" in BANNED_PHRASES
    assert "detect AI" in BANNED_PHRASES
    assert "the candidate used AI" in BANNED_PHRASES


# ---------------------------------------------------------------------------
# Prompt constants present
# ---------------------------------------------------------------------------


def test_prompt_constants_are_strings():
    assert isinstance(ALIGNMENT_SEMANTIC_SYSTEM_PROMPT, str)
    assert isinstance(ALIGNMENT_SUMMARY_PROMPT, str)
    assert isinstance(QUESTIONS_SYSTEM_PROMPT, str)
    assert isinstance(CHAT_SYSTEM_PROMPT, str)
    assert "JSON" in ALIGNMENT_SEMANTIC_SYSTEM_PROMPT
    assert "JSON" in QUESTIONS_SYSTEM_PROMPT
    # Sanity: the chat prompt explains the comparative-attribution framing
    assert "comparative" in CHAT_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# get_anthropic_client (backwards-compat shim over get_llm_provider)
# ---------------------------------------------------------------------------


def test_get_anthropic_client_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    assert get_anthropic_client() is None


def test_get_anthropic_client_with_key_no_package(monkeypatch):
    """If the key is set but the package isn't importable, returns None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)

    from importlib import import_module as real_import_module

    def fake_import_module(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("simulated")
        return real_import_module(name, *args, **kwargs)

    # The lazy import happens inside byline.llm_provider.AnthropicProvider.__init__.
    monkeypatch.setattr("byline.llm_provider.import_module", fake_import_module, raising=False)
    # Fallback: patch importlib's import_module reference directly so the
    # local ``from importlib import import_module`` inside the provider
    # __init__ resolves to our shim.
    import importlib

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    assert get_anthropic_client() is None


def test_get_anthropic_client_returns_provider(monkeypatch):
    """The shim returns an LLMProvider instance when the env is configured."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)

    # Stub out the AnthropicProvider so we don't actually instantiate the SDK.
    fake_provider = MagicMock(spec=LLMProvider)
    fake_provider.name = "anthropic"
    fake_provider.default_model = "claude-sonnet-4-5"

    import byline.llm_provider as lp

    monkeypatch.setattr(lp, "AnthropicProvider", lambda **kwargs: fake_provider)

    result = get_anthropic_client()
    assert result is fake_provider


# ---------------------------------------------------------------------------
# Mock provider helper
# ---------------------------------------------------------------------------


def make_mock_provider(*texts: str) -> MagicMock:
    """Mock provider whose ``generate()`` returns successive texts."""
    p = MagicMock(spec=LLMProvider)
    p.name = "mock"
    p.default_model = "mock"
    p.generate.side_effect = list(texts)
    return p


# ---------------------------------------------------------------------------
# run_alignment_semantic
# ---------------------------------------------------------------------------


def test_run_alignment_semantic_returns_tuple():
    checks_json = json.dumps(
        [
            {
                "kind": "doc_claims_feature_not_in_code",
                "description": "README claims X but no code implements it",
                "doc_location": "README.md:42",
                "code_location": None,
                "severity": "notable",
            }
        ]
    )
    summary_text = "Comparative signals indicate moderate divergence."
    provider = make_mock_provider(checks_json, summary_text)

    checks, summary = run_alignment_semantic(
        audit_summary={"score": 0.5},
        readme_text="# README\nclaims X",
        sampled_code={"main.py": "print('hi')"},
        provider=provider,
    )

    assert isinstance(checks, list)
    assert len(checks) == 1
    assert checks[0]["kind"] == "doc_claims_feature_not_in_code"
    assert summary == summary_text
    # Two calls: one for checks, one for summary
    assert provider.generate.call_count == 2

    # First call should have used the semantic prompt; second the summary prompt.
    first_call_args = provider.generate.call_args_list[0]
    second_call_args = provider.generate.call_args_list[1]
    assert first_call_args.args[0] == ALIGNMENT_SEMANTIC_SYSTEM_PROMPT
    assert second_call_args.args[0] == ALIGNMENT_SUMMARY_PROMPT


def test_run_alignment_semantic_post_processes_banned_phrases():
    checks_json = json.dumps([])
    summary_text = "This tool acts as an AI detector for hiring."
    provider = make_mock_provider(checks_json, summary_text)

    _checks, summary = run_alignment_semantic(
        audit_summary={},
        readme_text="",
        sampled_code={},
        provider=provider,
    )
    assert "AI detector" not in summary
    assert "[redacted]" in summary


def test_run_alignment_semantic_raises_on_none_provider():
    with pytest.raises(LLMUnavailableError):
        run_alignment_semantic(
            audit_summary={},
            readme_text="",
            sampled_code={},
            provider=None,
        )


def test_run_alignment_semantic_raises_on_malformed_json():
    provider = make_mock_provider("this is not json")
    with pytest.raises(LLMResponseError):
        run_alignment_semantic(
            audit_summary={},
            readme_text="",
            sampled_code={},
            provider=provider,
        )


# ---------------------------------------------------------------------------
# run_questions
# ---------------------------------------------------------------------------


def test_run_questions_returns_list_of_dicts():
    questions_json = json.dumps(
        [
            {
                "text": "Walk me through your retry logic in fetch.py.",
                "grounding_file": "fetch.py",
                "grounding_line": 42,
                "signal_addressed": "retry_complexity",
                "rationale": "Tests understanding of exception handling.",
            },
            {
                "text": "How did you decide on the cache TTL?",
                "grounding_file": "cache.py",
                "grounding_line": 10,
                "signal_addressed": "design_decision",
                "rationale": "Probes tradeoff reasoning.",
            },
            {
                "text": "What happens on database connection loss?",
                "grounding_file": "db.py",
                "grounding_line": None,
                "signal_addressed": "robustness",
                "rationale": "Operational concern.",
            },
        ]
    )
    provider = make_mock_provider(questions_json)

    qs = run_questions(
        audit_summary={},
        sampled_excerpts={"fetch.py": "..."},
        n=3,
        provider=provider,
    )

    assert isinstance(qs, list)
    assert len(qs) == 3
    assert qs[0]["grounding_file"] == "fetch.py"
    assert provider.generate.call_count == 1


def test_run_questions_post_processes_banned_phrases():
    questions_json = json.dumps(
        [
            {
                "text": "Does this read like an AI detector flagged it?",
                "grounding_file": "x.py",
                "grounding_line": 1,
                "signal_addressed": "x",
                "rationale": "y",
            }
        ]
    )
    provider = make_mock_provider(questions_json)

    qs = run_questions(
        audit_summary={},
        sampled_excerpts={},
        n=1,
        provider=provider,
    )
    assert "AI detector" not in qs[0]["text"]
    assert "[redacted]" in qs[0]["text"]


def test_run_questions_raises_on_none_provider():
    with pytest.raises(LLMUnavailableError):
        run_questions(
            audit_summary={},
            sampled_excerpts={},
            n=3,
            provider=None,
        )


def test_run_questions_raises_on_malformed_json():
    provider = make_mock_provider("not json")
    with pytest.raises(LLMResponseError):
        run_questions(
            audit_summary={},
            sampled_excerpts={},
            n=3,
            provider=provider,
        )


# ---------------------------------------------------------------------------
# run_chat_turn
# ---------------------------------------------------------------------------


def test_run_chat_turn_returns_text():
    provider = make_mock_provider("The audit found three significant findings.")
    reply = run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=[],
        user_message="Summarize the findings.",
        provider=provider,
    )
    assert reply == "The audit found three significant findings."


def test_run_chat_turn_post_processes_banned_phrases():
    provider = make_mock_provider("This is an AI detector that determines authorship.")
    reply = run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=[],
        user_message="What is byline?",
        provider=provider,
    )
    assert "AI detector" not in reply
    assert "[redacted]" in reply


def test_run_chat_turn_raises_on_none_provider():
    with pytest.raises(LLMUnavailableError):
        run_chat_turn(
            system_prompt=CHAT_SYSTEM_PROMPT,
            conversation_history=[],
            user_message="hi",
            provider=None,
        )


def test_run_chat_turn_passes_history():
    provider = make_mock_provider("ok")
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
    ]
    run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=history,
        user_message="second",
        provider=provider,
    )
    # Inspect the call: history is folded into the user content.
    args, kwargs = provider.generate.call_args
    # generate(system, user, *, max_tokens=..., temperature=..., model=...)
    user_content = args[1]
    assert "first" in user_content
    assert "answer" in user_content
    assert "second" in user_content
