"""Tests for the semantic (LLM-driven) half of byline.alignment (spec §5.3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from byline.alignment import check_alignment, run_semantic_alignment
from byline.llm import LLMUnavailableError
from byline.models import AlignmentCheck, AlignmentFindings

FIXTURES = Path(__file__).parent / "fixtures"
MISALIGNED = FIXTURES / "misaligned_docs_repo"


# ---------------------------------------------------------------------------
# Mock client helpers (mirrors test_llm_v02.py)
# ---------------------------------------------------------------------------


def _make_mock_client(text_responses: list[str]) -> MagicMock:
    """Mock anthropic-like client whose messages.create returns each text in turn."""

    client = MagicMock()
    responses = []
    for text in text_responses:
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage = MagicMock(input_tokens=100, output_tokens=50)
        responses.append(resp)
    client.messages.create.side_effect = responses
    return client


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
    client = _make_mock_client([_semantic_checks_payload(), summary_text])

    checks, summary = run_semantic_alignment(MISALIGNED, client)

    assert isinstance(checks, list)
    assert len(checks) >= 1
    for c in checks:
        assert isinstance(c, AlignmentCheck)
        assert c.source == "llm"
    assert summary == summary_text
    # One call for the checks JSON, one for the summary prose.
    assert client.messages.create.call_count == 2


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
    client = _make_mock_client([payload, "summary"])

    checks, summary = run_semantic_alignment(MISALIGNED, client)

    assert len(checks) == 1
    assert checks[0].description == "Valid entry"
    assert summary == "summary"


# ---------------------------------------------------------------------------
# run_semantic_alignment — failure modes degrade gracefully
# ---------------------------------------------------------------------------


def test_run_semantic_alignment_returns_empty_on_llm_unavailable() -> None:
    client = MagicMock()
    client.messages.create.side_effect = LLMUnavailableError("simulated")

    checks, summary = run_semantic_alignment(MISALIGNED, client)

    assert checks == []
    assert summary == ""


def test_run_semantic_alignment_returns_empty_on_none_client() -> None:
    # ``run_alignment_semantic`` raises LLMUnavailableError when the client is
    # None; ``run_semantic_alignment`` should swallow that and return empties.
    checks, summary = run_semantic_alignment(MISALIGNED, None)

    assert checks == []
    assert summary == ""


def test_run_semantic_alignment_returns_empty_on_non_json_output() -> None:
    """If the model returns non-JSON, llm raises LLMResponseError; we swallow it."""

    client = _make_mock_client(["not valid json", "summary"])

    checks, summary = run_semantic_alignment(MISALIGNED, client)

    assert checks == []
    assert summary == ""


# ---------------------------------------------------------------------------
# check_alignment — composes deterministic + semantic
# ---------------------------------------------------------------------------


def test_check_alignment_with_llm_includes_semantic_checks() -> None:
    summary_text = "Several documented behaviors are not implemented."
    client = _make_mock_client([_semantic_checks_payload(), summary_text])

    findings = check_alignment(MISALIGNED, with_llm=True, anthropic_client=client)

    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is False
    assert findings.llm_summary == summary_text
    sources = {c.source for c in findings.checks}
    # Both halves should be represented for the misaligned fixture.
    assert "deterministic" in sources
    assert "llm" in sources


def test_check_alignment_without_client_stays_deterministic_only() -> None:
    findings = check_alignment(MISALIGNED, with_llm=True, anthropic_client=None)

    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is True
    assert findings.llm_summary is None
    assert all(c.source == "deterministic" for c in findings.checks)


def test_check_alignment_with_llm_failure_still_reports_deterministic() -> None:
    """LLM failure should not lose the deterministic findings."""

    client = MagicMock()
    client.messages.create.side_effect = LLMUnavailableError("simulated")

    findings = check_alignment(MISALIGNED, with_llm=True, anthropic_client=client)

    # We did attempt the LLM, so deterministic_only is False even though it
    # produced nothing useful. The deterministic checks are still present.
    assert findings.deterministic_only is False
    assert findings.llm_summary is None
    assert any(c.source == "deterministic" for c in findings.checks)
    assert not any(c.source == "llm" for c in findings.checks)
