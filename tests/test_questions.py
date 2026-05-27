"""Tests for byline.questions — LLM-required interview question generator (v0.2 §5.7)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from byline.llm import LLMUnavailableError
from byline.models import (
    AlignmentCheck,
    AlignmentFindings,
    AuditResult,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    Question,
    QuestionSet,
    StyleProfile,
)
from byline.questions import generate_questions


# ---------------------------------------------------------------------------
# Helpers
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


def _make_style_profile() -> StyleProfile:
    return StyleProfile(
        em_dash_density=0.5,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=15.0,
        type_token_ratio=0.6,
        typo_rate=0.0,
        sophistication_score=0.5,
        banner_comment_density=0.1,
        progress_ux_score=0.2,
    )


def _make_audit(
    target_repo: str,
    *,
    fingerprints: list[FingerprintHit] | None = None,
    disproportions: list[DisproportionFinding] | None = None,
    deltas: list[ComparativeDelta] | None = None,
    alignment: AlignmentFindings | None = None,
) -> AuditResult:
    return AuditResult(
        target_repo=target_repo,
        candidate="octocat",
        baseline=None,
        target_profile=_make_style_profile(),
        fingerprints=fingerprints or [],
        disproportions=disproportions or [],
        deltas=deltas or [],
        llm_qualitative=None,
        overall_signal="mixed",
        alignment=alignment,
    )


def _build_synthetic_repo(tmp_path: Path) -> Path:
    """Build a tiny repo with a docker-compose file, a shell script, and a Python file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(
        "version: '3'\n"
        "services:\n"
        "  web:\n"
        "    image: nginx\n"
        "    ports:\n"
        "      - '80:80'\n"
    )
    (repo / "deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'deploying'\n"
        "docker compose up -d\n"
    )
    (repo / "app.py").write_text(
        "def main() -> None:\n"
        "    print('hello')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_generate_questions_raises_when_client_is_none(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    audit = _make_audit(str(repo))
    with pytest.raises(LLMUnavailableError):
        generate_questions(audit, repo, anthropic_client=None, n=5)


def test_generate_questions_returns_question_set(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    fingerprints = [
        FingerprintHit(
            file_path="docker-compose.yml",
            line_number=2,
            pattern="services_section",
            category="structure",
            excerpt="services:",
        ),
        FingerprintHit(
            file_path="deploy.sh",
            line_number=3,
            pattern="banner_echo",
            category="shell_banner",
            excerpt="echo 'deploying'",
        ),
        FingerprintHit(
            file_path="app.py",
            line_number=1,
            pattern="def_main",
            category="phrase",
            excerpt="def main()",
        ),
    ]
    audit = _make_audit(str(repo), fingerprints=fingerprints)

    question_objs = [
        {
            "text": f"Question {i}?",
            "grounding_file": "app.py",
            "grounding_line": i,
            "signal_addressed": f"signal_{i}",
            "rationale": "Because.",
        }
        for i in range(5)
    ]
    client = _make_mock_client([json.dumps(question_objs)])

    result = generate_questions(audit, repo, anthropic_client=client, n=5)

    assert isinstance(result, QuestionSet)
    assert len(result.questions) == 5
    for q in result.questions:
        assert isinstance(q, Question)
    assert isinstance(result.generated_at, datetime)
    # generated_at should be timezone-aware UTC
    assert result.generated_at.tzinfo is not None


def test_generate_questions_summary_shape(tmp_path: Path) -> None:
    """Capture the audit_summary handed to the LLM and inspect its shape."""
    repo = _build_synthetic_repo(tmp_path)

    # Build > 5 fingerprints across diverse categories to test top_fingerprints cap.
    fingerprints = [
        FingerprintHit(
            file_path="docker-compose.yml",
            line_number=2,
            pattern="services_section",
            category="structure",
            excerpt="services:",
        ),
        FingerprintHit(
            file_path="deploy.sh",
            line_number=3,
            pattern="banner_echo",
            category="shell_banner",
            excerpt="echo 'deploying'",
        ),
        FingerprintHit(
            file_path="app.py",
            line_number=1,
            pattern="def_main",
            category="phrase",
            excerpt="def main()",
        ),
        FingerprintHit(
            file_path="app.py",
            line_number=2,
            pattern="progress_bar",
            category="progress_ux",
            excerpt="...",
        ),
        FingerprintHit(
            file_path="docker-compose.yml",
            line_number=3,
            pattern="ai_header",
            category="ai_section_header",
            excerpt="# Generated",
        ),
        FingerprintHit(
            file_path="app.py",
            line_number=3,
            pattern="extra_phrase",
            category="phrase",
            excerpt="x",
        ),
        FingerprintHit(
            file_path="app.py",
            line_number=4,
            pattern="extra_phrase2",
            category="phrase",
            excerpt="y",
        ),
    ]

    # 4 disproportions with mixed severities → top 3 by severity weight
    disproportions = [
        DisproportionFinding(
            name="d_info",
            observed=0.1,
            threshold=0.1,
            severity="info",
            description="info one",
        ),
        DisproportionFinding(
            name="d_notable",
            observed=0.2,
            threshold=0.1,
            severity="notable",
            description="notable one",
        ),
        DisproportionFinding(
            name="d_significant",
            observed=0.3,
            threshold=0.1,
            severity="significant",
            description="significant one",
        ),
        DisproportionFinding(
            name="d_significant_2",
            observed=0.4,
            threshold=0.1,
            severity="significant",
            description="significant two",
        ),
    ]

    # 5 deltas with mixed severities → top 3 by severity weight (extreme > significant > notable > aligned)
    deltas = [
        ComparativeDelta(
            metric="m_aligned",
            baseline_value=1.0,
            target_value=1.0,
            absolute_delta=0.0,
            relative_delta=0.0,
            severity="aligned",
        ),
        ComparativeDelta(
            metric="m_notable",
            baseline_value=1.0,
            target_value=1.5,
            absolute_delta=0.5,
            relative_delta=0.5,
            severity="notable",
        ),
        ComparativeDelta(
            metric="m_significant",
            baseline_value=1.0,
            target_value=2.0,
            absolute_delta=1.0,
            relative_delta=1.0,
            severity="significant",
        ),
        ComparativeDelta(
            metric="m_extreme",
            baseline_value=1.0,
            target_value=5.0,
            absolute_delta=4.0,
            relative_delta=4.0,
            severity="extreme",
        ),
        ComparativeDelta(
            metric="m_extreme_2",
            baseline_value=1.0,
            target_value=6.0,
            absolute_delta=5.0,
            relative_delta=5.0,
            severity="extreme",
        ),
    ]

    # AlignmentFindings with checks of varied severity → only severity != info
    alignment = AlignmentFindings(
        checks=[
            AlignmentCheck(
                kind="cli_flag_documented_missing_in_code",
                source="deterministic",
                description="ignored info",
                doc_location="README.md:1",
                code_location=None,
                severity="info",
            ),
            AlignmentCheck(
                kind="doc_claims_feature_not_in_code",
                source="llm",
                description="kept notable",
                doc_location="README.md:5",
                code_location=None,
                severity="notable",
            ),
            AlignmentCheck(
                kind="config_documented_but_unused",
                source="llm",
                description="kept significant",
                doc_location="README.md:9",
                code_location=None,
                severity="significant",
            ),
        ],
        deterministic_only=False,
        overall_alignment="minor_gaps",
        llm_summary=None,
    )

    audit = _make_audit(
        str(repo),
        fingerprints=fingerprints,
        disproportions=disproportions,
        deltas=deltas,
        alignment=alignment,
    )

    question_objs = [
        {
            "text": "Q?",
            "grounding_file": "app.py",
            "grounding_line": 1,
            "signal_addressed": "s",
            "rationale": "r",
        }
    ]
    client = _make_mock_client([json.dumps(question_objs)])

    generate_questions(audit, repo, anthropic_client=client, n=1)

    # Inspect what was sent to the model. run_questions composes a string
    # containing JSON-serialised audit_summary, so we can parse it back out.
    _args, kwargs = client.messages.create.call_args
    user_content = kwargs["messages"][0]["content"]
    assert "Audit summary (JSON):" in user_content
    # Extract the JSON block following the marker, up to the next blank line / section
    json_blob_start = user_content.index("Audit summary (JSON):") + len(
        "Audit summary (JSON):"
    )
    rest = user_content[json_blob_start:].lstrip()
    # The blob is followed by "\n\nSampled code excerpts:"
    end = rest.index("\n\nSampled code excerpts:")
    audit_summary_json = rest[:end].strip()
    summary = json.loads(audit_summary_json)

    assert summary["target_repo"] == str(repo)
    assert summary["candidate"] == "octocat"
    assert summary["overall_signal"] == "mixed"
    assert "top_fingerprints" in summary
    assert len(summary["top_fingerprints"]) <= 5
    assert "top_disproportions" in summary
    assert len(summary["top_disproportions"]) == 3
    # Top 3 disproportions should not contain the "info" entry
    sev_names = {d["severity"] for d in summary["top_disproportions"]}
    assert "info" not in sev_names
    assert "top_deltas" in summary
    assert len(summary["top_deltas"]) == 3
    # Top 3 deltas should not contain the "aligned" entry
    delta_sev_names = {d["severity"] for d in summary["top_deltas"]}
    assert "aligned" not in delta_sev_names
    # alignment_gaps must drop info-severity check
    assert "alignment_gaps" in summary
    assert len(summary["alignment_gaps"]) == 2
    for gap in summary["alignment_gaps"]:
        assert gap["severity"] != "info"

    # Verify excerpts were included; each annotated file should show "1: " line prefix
    assert "Sampled code excerpts:" in user_content
    assert "1: " in user_content  # line numbering present


def test_generate_questions_tolerates_malformed_entries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = _build_synthetic_repo(tmp_path)
    audit = _make_audit(str(repo))

    question_objs = [
        {
            "text": "Q1",
            "grounding_file": "app.py",
            "grounding_line": 1,
            "signal_addressed": "s",
            "rationale": "r",
        },
        {
            "text": "Q2",
            "grounding_file": "app.py",
            "grounding_line": 2,
            "signal_addressed": "s",
            "rationale": "r",
        },
        {
            "text": "Q3",
            "grounding_file": "app.py",
            "grounding_line": 3,
            "signal_addressed": "s",
            "rationale": "r",
        },
        {
            "text": "Q4",
            "grounding_file": "app.py",
            "grounding_line": 4,
            "signal_addressed": "s",
            "rationale": "r",
        },
        # Malformed: missing required "text" field
        {
            "grounding_file": "app.py",
            "grounding_line": 5,
            "signal_addressed": "s",
            "rationale": "r",
        },
    ]
    client = _make_mock_client([json.dumps(question_objs)])

    with caplog.at_level(logging.WARNING, logger="byline.questions"):
        result = generate_questions(audit, repo, anthropic_client=client, n=5)

    assert len(result.questions) == 4
    assert any("malformed" in rec.message.lower() for rec in caplog.records)


def test_generate_questions_no_top_level_anthropic_import() -> None:
    """The questions module must never import anthropic at top level."""
    src = Path("byline/questions.py").read_text()
    # Look only at top-level statements (no leading whitespace) for the import
    for line in src.splitlines():
        if line.startswith("import anthropic") or line.startswith("from anthropic"):
            raise AssertionError("byline/questions.py must not import anthropic at top level")
