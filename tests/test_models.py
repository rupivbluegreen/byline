"""Tests for byline.models — pydantic v2 data models per spec §5."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from byline.models import (
    AuditResult,
    BaselineCorpus,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    MetricResult,
    StyleProfile,
    WritingSample,
)

# ---------------------------------------------------------------------------
# WritingSample
# ---------------------------------------------------------------------------


def test_writing_sample_minimal() -> None:
    sample = WritingSample(
        source="README.md@octocat/hello-world",
        kind="readme",
        text="hello world",
        word_count=2,
    )
    assert sample.source == "README.md@octocat/hello-world"
    assert sample.kind == "readme"
    assert sample.word_count == 2


def test_writing_sample_invalid_kind_raises() -> None:
    with pytest.raises(ValidationError):
        WritingSample(
            source="foo",
            kind="not_a_valid_kind",  # type: ignore[arg-type]
            text="x",
            word_count=1,
        )


# ---------------------------------------------------------------------------
# BaselineCorpus
# ---------------------------------------------------------------------------


def test_baseline_corpus_minimal() -> None:
    sample = WritingSample(source="x", kind="comment", text="hi", word_count=1)
    corpus = BaselineCorpus(
        username="octocat",
        samples=[sample],
        total_words=1,
        repos_scanned=["octocat/hello-world"],
        repos_skipped=[("octocat/skipme", "fork")],
    )
    assert corpus.username == "octocat"
    assert corpus.total_words == 1
    assert corpus.repos_skipped == [("octocat/skipme", "fork")]
    assert corpus.samples[0].text == "hi"


# ---------------------------------------------------------------------------
# MetricResult
# ---------------------------------------------------------------------------


def test_metric_result_minimal() -> None:
    m = MetricResult(
        name="em_dash_density",
        value=1.5,
        unit="per 1000 words",
        description="Frequency of em dashes per 1000 words.",
    )
    assert m.name == "em_dash_density"
    assert m.value == 1.5
    assert m.unit == "per 1000 words"


# ---------------------------------------------------------------------------
# StyleProfile
# ---------------------------------------------------------------------------


def test_style_profile_minimal() -> None:
    profile = StyleProfile(
        em_dash_density=0.5,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=18.2,
        type_token_ratio=0.62,
        typo_rate=0.01,
        sophistication_score=11.3,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )
    assert profile.avg_sentence_length == 18.2
    assert profile.type_token_ratio == 0.62


# ---------------------------------------------------------------------------
# FingerprintHit
# ---------------------------------------------------------------------------


def test_fingerprint_hit_minimal() -> None:
    hit = FingerprintHit(
        file_path="README.md",
        line_number=42,
        pattern="delve into",
        category="phrase",
        excerpt="Let us delve into the architecture.",
    )
    assert hit.file_path == "README.md"
    assert hit.line_number == 42
    assert hit.category == "phrase"


def test_fingerprint_hit_allows_none_line_number() -> None:
    hit = FingerprintHit(
        file_path="x.md",
        line_number=None,
        pattern="p",
        category="structure",
        excerpt="ex",
    )
    assert hit.line_number is None


def test_fingerprint_hit_invalid_category_raises() -> None:
    with pytest.raises(ValidationError):
        FingerprintHit(
            file_path="x",
            line_number=1,
            pattern="p",
            category="not_a_category",  # type: ignore[arg-type]
            excerpt="ex",
        )


# ---------------------------------------------------------------------------
# DisproportionFinding
# ---------------------------------------------------------------------------


def test_disproportion_finding_minimal() -> None:
    finding = DisproportionFinding(
        name="readme_size_vs_code",
        observed=4.2,
        threshold=2.0,
        severity="significant",
        description="README is unusually large relative to code volume.",
    )
    assert finding.name == "readme_size_vs_code"
    assert finding.severity == "significant"


def test_disproportion_finding_invalid_severity_raises() -> None:
    with pytest.raises(ValidationError):
        DisproportionFinding(
            name="x",
            observed=1.0,
            threshold=1.0,
            severity="critical",  # type: ignore[arg-type]
            description="d",
        )


# ---------------------------------------------------------------------------
# ComparativeDelta
# ---------------------------------------------------------------------------


def test_comparative_delta_minimal() -> None:
    delta = ComparativeDelta(
        metric="em_dash_density",
        baseline_value=0.5,
        target_value=4.0,
        absolute_delta=3.5,
        relative_delta=7.0,
        severity="extreme",
    )
    assert delta.metric == "em_dash_density"
    assert delta.severity == "extreme"
    assert delta.absolute_delta == 3.5


def test_comparative_delta_invalid_severity_raises() -> None:
    with pytest.raises(ValidationError):
        ComparativeDelta(
            metric="m",
            baseline_value=0.0,
            target_value=0.0,
            absolute_delta=0.0,
            relative_delta=0.0,
            severity="huge",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# AuditResult
# ---------------------------------------------------------------------------


def _style_profile() -> StyleProfile:
    return StyleProfile(
        em_dash_density=0.0,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=0.0,
        type_token_ratio=0.0,
        typo_rate=0.0,
        sophistication_score=0.0,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )


def test_audit_result_minimal() -> None:
    result = AuditResult(
        target_repo="octocat/hello-world",
        candidate=None,
        baseline=None,
        target_profile=_style_profile(),
        fingerprints=[],
        disproportions=[],
        deltas=[],
        llm_qualitative=None,
        overall_signal="aligned",
    )
    assert result.target_repo == "octocat/hello-world"
    assert result.overall_signal == "aligned"
    assert result.candidate is None
    assert result.baseline is None


def test_audit_result_generated_at_defaults_to_datetime() -> None:
    result = AuditResult(
        target_repo="r",
        candidate=None,
        baseline=None,
        target_profile=_style_profile(),
        fingerprints=[],
        disproportions=[],
        deltas=[],
        llm_qualitative=None,
        overall_signal="mixed",
    )
    assert isinstance(result.generated_at, datetime)
    assert result.generated_at.tzinfo is not None
    # Should be UTC-aware
    assert result.generated_at.utcoffset() == timezone.utc.utcoffset(result.generated_at)


def test_audit_result_invalid_overall_signal_raises() -> None:
    with pytest.raises(ValidationError):
        AuditResult(
            target_repo="r",
            candidate=None,
            baseline=None,
            target_profile=_style_profile(),
            fingerprints=[],
            disproportions=[],
            deltas=[],
            llm_qualitative=None,
            overall_signal="totally_fine",  # type: ignore[arg-type]
        )
