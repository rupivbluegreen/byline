"""Tests for byline.llm — optional Anthropic qualitative pass per spec §10."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from byline.llm import qualitative_pass
from byline.models import (
    AuditResult,
    BaselineCorpus,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    StyleProfile,
    WritingSample,
)

# ---------------------------------------------------------------------------
# Fixture builders (mirrors test_report.py patterns).
# ---------------------------------------------------------------------------


def _profile() -> StyleProfile:
    return StyleProfile(
        em_dash_density=2.0,
        emoji_in_headers_ratio=0.1,
        avg_sentence_length=15.0,
        type_token_ratio=0.5,
        typo_rate=1.0,
        sophistication_score=0.3,
        banner_comment_density=1.0,
        progress_ux_score=0.25,
    )


def _baseline() -> BaselineCorpus:
    return BaselineCorpus(
        username="alice",
        samples=[
            WritingSample(
                source="README.md@alice/proj-one",
                kind="readme",
                text="hello world",
                word_count=2,
            ),
        ],
        total_words=2,
        repos_scanned=["alice/proj-one"],
        repos_skipped=[],
    )


def _deltas() -> list[ComparativeDelta]:
    return [
        ComparativeDelta(
            metric="em_dash_density",
            baseline_value=0.0,
            target_value=12.5,
            absolute_delta=12.5,
            relative_delta=float("inf"),
            severity="extreme",
        ),
        ComparativeDelta(
            metric="emoji_in_headers_ratio",
            baseline_value=0.1,
            target_value=0.6,
            absolute_delta=0.5,
            relative_delta=5.0,
            severity="significant",
        ),
    ]


def _fingerprints() -> list[FingerprintHit]:
    return [
        FingerprintHit(
            file_path="README.md",
            line_number=12,
            pattern="dive into",
            category="phrase",
            excerpt="Let's dive into this project",
        ),
        FingerprintHit(
            file_path="README.md",
            line_number=18,
            pattern="dive into",
            category="phrase",
            excerpt="Let's dive into the details",
        ),
        FingerprintHit(
            file_path="README.md",
            line_number=22,
            pattern="elevate",
            category="phrase",
            excerpt="elevate your workflow",
        ),
        FingerprintHit(
            file_path="setup.sh",
            line_number=1,
            pattern="banner-comment-block",
            category="shell_banner",
            excerpt="#" * 40,
        ),
    ]


def _disproportions() -> list[DisproportionFinding]:
    return [
        DisproportionFinding(
            name="docs_to_code_ratio",
            observed=4.2,
            threshold=2.0,
            severity="significant",
            description="Documentation lines exceed code lines by an unusual margin.",
        ),
    ]


def _audit_result() -> AuditResult:
    return AuditResult(
        target_repo="alice/take-home-submission",
        candidate="alice",
        baseline=_baseline(),
        target_profile=_profile(),
        fingerprints=_fingerprints(),
        disproportions=_disproportions(),
        deltas=_deltas(),
        llm_qualitative=None,
        overall_signal="divergent",
        generated_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_qualitative_pass_returns_none_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert qualitative_pass(_audit_result()) is None


def test_qualitative_pass_returns_none_when_anthropic_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr("byline.llm.import_module", fake_import_module)
    assert qualitative_pass(_audit_result()) is None


def test_qualitative_pass_returns_text_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_block = MagicMock()
    mock_block.text = "The signals suggest divergence between baseline and target."
    mock_message.content = [mock_block]
    mock_client.messages.create.return_value = mock_message
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    result = qualitative_pass(_audit_result())
    assert result == "The signals suggest divergence between baseline and target."

    call = mock_client.messages.create.call_args
    assert call.kwargs["model"] == "claude-sonnet-4-5"
    assert call.kwargs["temperature"] == 0.2
    assert call.kwargs["max_tokens"] == 600


def test_qualitative_pass_user_message_contains_structured_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="ok")]
    mock_client.messages.create.return_value = mock_message
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    qualitative_pass(_audit_result())
    call = mock_client.messages.create.call_args
    user_content = call.kwargs["messages"][0]["content"]
    assert "alice/take-home-submission" in user_content
    assert "alice" in user_content
    assert "divergent" in user_content
    assert "em_dash_density" in user_content
    assert "docs_to_code_ratio" in user_content
    # Fingerprint aggregation by category.
    assert "phrase" in user_content
    # Top patterns aggregation — "dive into" appears twice.
    assert "dive into" in user_content


def test_qualitative_pass_system_prompt_enforces_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="ok")]
    mock_client.messages.create.return_value = mock_message
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    qualitative_pass(_audit_result())
    call = mock_client.messages.create.call_args
    system_prompt = call.kwargs["system"]
    lowered = system_prompt.lower()
    # Required framing vocabulary.
    assert "divergence" in lowered or "signals" in lowered
    # Prohibitions visible to LLM (these appear in the system prompt as rules,
    # which is the only place they're allowed in this module).
    assert "ai detector" in lowered
    assert "comparative" in lowered


def test_qualitative_pass_returns_none_on_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    import anthropic

    mock_client = MagicMock()

    def raise_api_error(*args, **kwargs):  # type: ignore[no-untyped-def]
        # APIError requires a message + request + body in newer SDKs; subclass
        # to keep instantiation cheap.
        raise anthropic.APIError(  # type: ignore[call-arg]
            message="boom",
            request=MagicMock(),
            body=None,
        )

    mock_client.messages.create.side_effect = raise_api_error
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    assert qualitative_pass(_audit_result()) is None


def test_qualitative_pass_returns_none_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = ConnectionError("network down")
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    assert qualitative_pass(_audit_result()) is None


def test_qualitative_pass_handles_candidate_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="ok")]
    mock_client.messages.create.return_value = mock_message
    monkeypatch.setattr("anthropic.Anthropic", lambda: mock_client)

    result = _audit_result()
    result_no_candidate = result.model_copy(update={"candidate": None})
    qualitative_pass(result_no_candidate)

    call = mock_client.messages.create.call_args
    user_content = call.kwargs["messages"][0]["content"]
    assert "not provided" in user_content
