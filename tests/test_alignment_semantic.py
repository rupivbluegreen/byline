"""Tests for the semantic (LLM-driven) half of byline.alignment (spec §5.3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from byline.alignment import check_alignment, run_semantic_alignment
from byline.llm import LLMUnavailableError
from byline.llm_provider import LLMProvider
from byline.models import AlignmentCheck, AlignmentFindings

FIXTURES = Path(__file__).parent / "fixtures"
MISALIGNED = FIXTURES / "misaligned_docs_repo"


# ---------------------------------------------------------------------------
# Mock provider helpers (mirrors test_llm_v02.py)
# ---------------------------------------------------------------------------


def _make_mock_provider(text_responses: list[str]) -> MagicMock:
    """Mock LLMProvider whose ``generate()`` returns each text in turn."""

    p = MagicMock(spec=LLMProvider)
    p.name = "mock"
    p.default_model = "mock"
    p.generate.side_effect = list(text_responses)
    return p


def _semantic_checks_payload() -> str:
    """A small but realistic JSON payload of two semantic alignment checks."""

    return json.dumps(
        [
            {
                "kind": "doc_claims_feature_not_in_code",
                "description": "README claims --legacy-mode but code never reads it",
                "doc_location": "README.md",
                "code_location": "app.py",
                "severity": "significant",
            },
            {
                "kind": "config_documented_but_unused",
                "description": "MAGIC_TOKEN env var documented but app.py never reads it",
                "doc_location": "README.md",
                "code_location": None,
                "severity": "notable",
            },
        ]
    )


# ---------------------------------------------------------------------------
# run_semantic_alignment — happy path
# ---------------------------------------------------------------------------


def test_run_semantic_alignment_returns_checks_and_summary() -> None:
    summary_text = "The README overstates several features the code does not implement."
    provider = _make_mock_provider([_semantic_checks_payload(), summary_text])

    checks, summary = run_semantic_alignment(MISALIGNED, provider)

    assert isinstance(checks, list)
    assert len(checks) >= 1
    for c in checks:
        assert isinstance(c, AlignmentCheck)
        assert c.source == "llm"
    assert summary == summary_text
    # One call for the checks JSON, one for the summary prose.
    assert provider.generate.call_count == 2
    # First call uses the semantic prompt, second uses the summary prompt;
    # both must request a non-trivial max_tokens budget.
    first_call = provider.generate.call_args_list[0]
    assert first_call.kwargs["max_tokens"] >= 500


def test_run_semantic_alignment_skips_malformed_entries() -> None:
    """Entries missing required fields are tolerated, not fatal."""

    payload = json.dumps(
        [
            {
                "kind": "doc_claims_feature_not_in_code",
                "description": "Valid entry",
                "doc_location": "README.md",
                "code_location": None,
                "severity": "notable",
            },
            # Missing 'severity' — should be skipped.
            {
                "kind": "doc_claims_feature_not_in_code",
                "description": "Malformed (no severity)",
                "doc_location": "README.md",
                "code_location": None,
            },
            # Wrong type — should be skipped.
            "not even a dict",
        ]
    )
    provider = _make_mock_provider([payload, "summary"])

    checks, summary = run_semantic_alignment(MISALIGNED, provider)

    assert len(checks) == 1
    assert checks[0].description == "Valid entry"
    assert summary == "summary"


# ---------------------------------------------------------------------------
# run_semantic_alignment — failure modes degrade gracefully
# ---------------------------------------------------------------------------


def test_run_semantic_alignment_returns_empty_on_llm_unavailable() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.name = "mock"
    provider.default_model = "mock"
    provider.generate.side_effect = LLMUnavailableError("simulated")

    checks, summary = run_semantic_alignment(MISALIGNED, provider)

    assert checks == []
    assert summary == ""


def test_run_semantic_alignment_returns_empty_on_none_provider() -> None:
    # ``run_alignment_semantic`` raises LLMUnavailableError when the provider
    # is None; ``run_semantic_alignment`` should swallow that and return empties.
    checks, summary = run_semantic_alignment(MISALIGNED, None)

    assert checks == []
    assert summary == ""


def test_run_semantic_alignment_returns_empty_on_non_json_output() -> None:
    """If the model returns non-JSON, llm raises LLMResponseError; we swallow it."""

    provider = _make_mock_provider(["not valid json", "summary"])

    checks, summary = run_semantic_alignment(MISALIGNED, provider)

    assert checks == []
    assert summary == ""


# ---------------------------------------------------------------------------
# check_alignment — composes deterministic + semantic
# ---------------------------------------------------------------------------


def test_check_alignment_with_llm_includes_semantic_checks() -> None:
    summary_text = "Several documented behaviors are not implemented."
    provider = _make_mock_provider([_semantic_checks_payload(), summary_text])

    findings = check_alignment(MISALIGNED, with_llm=True, provider=provider)

    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is False
    assert findings.llm_summary == summary_text
    sources = {c.source for c in findings.checks}
    # Both halves should be represented for the misaligned fixture.
    assert "deterministic" in sources
    assert "llm" in sources


def test_check_alignment_without_provider_stays_deterministic_only() -> None:
    findings = check_alignment(MISALIGNED, with_llm=True, provider=None)

    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is True
    assert findings.llm_summary is None
    assert all(c.source == "deterministic" for c in findings.checks)


def test_check_alignment_with_llm_failure_still_reports_deterministic() -> None:
    """LLM failure should not lose the deterministic findings."""

    provider = MagicMock(spec=LLMProvider)
    provider.name = "mock"
    provider.default_model = "mock"
    provider.generate.side_effect = LLMUnavailableError("simulated")

    findings = check_alignment(MISALIGNED, with_llm=True, provider=provider)

    # We did attempt the LLM, so deterministic_only is False even though it
    # produced nothing useful. The deterministic checks are still present.
    assert findings.deterministic_only is False
    assert findings.llm_summary is None
    assert any(c.source == "deterministic" for c in findings.checks)
    assert not any(c.source == "llm" for c in findings.checks)


def test_check_alignment_accepts_deprecated_anthropic_client_alias() -> None:
    """``anthropic_client=`` keeps working as a deprecated alias for ``provider=``."""

    summary_text = "alias kept working"
    provider = _make_mock_provider([_semantic_checks_payload(), summary_text])

    findings = check_alignment(MISALIGNED, with_llm=True, anthropic_client=provider)

    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is False
    assert findings.llm_summary == summary_text
