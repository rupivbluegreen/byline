"""Tests for v0.2 LLM additions: prompts, post-processing, and helpers (§7)."""

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
# get_anthropic_client
# ---------------------------------------------------------------------------


def test_get_anthropic_client_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get_anthropic_client() is None


def test_get_anthropic_client_with_key_no_package(monkeypatch):
    """If the key is set but the package isn't importable, returns None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    # byline.llm imports ``import_module`` at module top level, so patch the
    # reference inside byline.llm directly.
    from importlib import import_module as real_import_module

    def fake_import_module(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("simulated")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr("byline.llm.import_module", fake_import_module)
    assert get_anthropic_client() is None


# ---------------------------------------------------------------------------
# Mock client helpers
# ---------------------------------------------------------------------------


def _make_mock_client(text_responses: list[str]) -> MagicMock:
    """Return a mock anthropic-like client whose messages.create returns the
    successive text_responses on each call."""
    client = MagicMock()
    responses = []
    for text in text_responses:
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage = MagicMock(input_tokens=100, output_tokens=50)
        responses.append(resp)
    client.messages.create.side_effect = responses
    return client


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
    client = _make_mock_client([checks_json, summary_text])

    checks, summary = run_alignment_semantic(
        audit_summary={"score": 0.5},
        readme_text="# README\nclaims X",
        sampled_code={"main.py": "print('hi')"},
        anthropic_client=client,
    )

    assert isinstance(checks, list)
    assert len(checks) == 1
    assert checks[0]["kind"] == "doc_claims_feature_not_in_code"
    assert summary == summary_text
    # Two calls: one for checks, one for summary
    assert client.messages.create.call_count == 2


def test_run_alignment_semantic_post_processes_banned_phrases():
    checks_json = json.dumps([])
    summary_text = "This tool acts as an AI detector for hiring."
    client = _make_mock_client([checks_json, summary_text])

    _checks, summary = run_alignment_semantic(
        audit_summary={},
        readme_text="",
        sampled_code={},
        anthropic_client=client,
    )
    assert "AI detector" not in summary
    assert "[redacted]" in summary


def test_run_alignment_semantic_raises_on_none_client():
    with pytest.raises(LLMUnavailableError):
        run_alignment_semantic(
            audit_summary={},
            readme_text="",
            sampled_code={},
            anthropic_client=None,
        )


def test_run_alignment_semantic_raises_on_malformed_json():
    client = _make_mock_client(["this is not json"])
    with pytest.raises(LLMResponseError):
        run_alignment_semantic(
            audit_summary={},
            readme_text="",
            sampled_code={},
            anthropic_client=client,
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
    client = _make_mock_client([questions_json])

    qs = run_questions(
        audit_summary={},
        sampled_excerpts={"fetch.py": "..."},
        n=3,
        anthropic_client=client,
    )

    assert isinstance(qs, list)
    assert len(qs) == 3
    assert qs[0]["grounding_file"] == "fetch.py"


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
    client = _make_mock_client([questions_json])

    qs = run_questions(
        audit_summary={},
        sampled_excerpts={},
        n=1,
        anthropic_client=client,
    )
    assert "AI detector" not in qs[0]["text"]
    assert "[redacted]" in qs[0]["text"]


def test_run_questions_raises_on_none_client():
    with pytest.raises(LLMUnavailableError):
        run_questions(
            audit_summary={},
            sampled_excerpts={},
            n=3,
            anthropic_client=None,
        )


def test_run_questions_raises_on_malformed_json():
    client = _make_mock_client(["not json"])
    with pytest.raises(LLMResponseError):
        run_questions(
            audit_summary={},
            sampled_excerpts={},
            n=3,
            anthropic_client=client,
        )


# ---------------------------------------------------------------------------
# run_chat_turn
# ---------------------------------------------------------------------------


def test_run_chat_turn_returns_text():
    client = _make_mock_client(["The audit found three significant findings."])
    reply = run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=[],
        user_message="Summarize the findings.",
        anthropic_client=client,
    )
    assert reply == "The audit found three significant findings."


def test_run_chat_turn_post_processes_banned_phrases():
    client = _make_mock_client(["This is an AI detector that determines authorship."])
    reply = run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=[],
        user_message="What is byline?",
        anthropic_client=client,
    )
    assert "AI detector" not in reply
    assert "[redacted]" in reply


def test_run_chat_turn_raises_on_none_client():
    with pytest.raises(LLMUnavailableError):
        run_chat_turn(
            system_prompt=CHAT_SYSTEM_PROMPT,
            conversation_history=[],
            user_message="hi",
            anthropic_client=None,
        )


def test_run_chat_turn_passes_history():
    client = _make_mock_client(["ok"])
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
    ]
    run_chat_turn(
        system_prompt=CHAT_SYSTEM_PROMPT,
        conversation_history=history,
        user_message="second",
        anthropic_client=client,
    )
    # Inspect the call to verify messages were passed
    _args, kwargs = client.messages.create.call_args
    messages = kwargs["messages"]
    # The history + new user message should be present
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "second"
    assert any(m.get("content") == "first" for m in messages)
