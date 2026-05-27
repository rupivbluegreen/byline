"""Tests for byline.models — pydantic v2 data models per spec §5."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from byline.models import (
    AlignmentCheck,
    AlignmentFindings,
    AuditResult,
    AuthorIdentityFinding,
    BaselineCorpus,
    BoilerplateFinding,
    CommitMessageStyleFinding,
    CommitTimelineFinding,
    ComparativeDelta,
    DisproportionFinding,
    FileEvolutionFinding,
    FingerprintHit,
    HistoryFindings,
    MetricResult,
    Question,
    QuestionSet,
    SelfBaselineFinding,
    StyleProfile,
    VoiceFinding,
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


# ---------------------------------------------------------------------------
# CommitTimelineFinding
# ---------------------------------------------------------------------------


def test_commit_timeline_finding_minimal() -> None:
    finding = CommitTimelineFinding(
        total_commits=10,
        span_seconds=3600.0,
        burst_density=0.5,
        burst_window_start=None,
        bursty=False,
        first_commit_file_count=3,
        first_commit_loc=120,
        first_commit_appears_pasted=False,
    )
    assert finding.total_commits == 10
    assert finding.burst_window_start is None
    assert finding.bursty is False


def test_commit_timeline_finding_with_window_start() -> None:
    now = datetime.now(timezone.utc)
    finding = CommitTimelineFinding(
        total_commits=1,
        span_seconds=0.0,
        burst_density=1.0,
        burst_window_start=now,
        bursty=True,
        first_commit_file_count=50,
        first_commit_loc=5000,
        first_commit_appears_pasted=True,
    )
    assert finding.burst_window_start == now
    assert finding.first_commit_appears_pasted is True


# ---------------------------------------------------------------------------
# CommitMessageStyleFinding
# ---------------------------------------------------------------------------


def test_commit_message_style_finding_minimal() -> None:
    finding = CommitMessageStyleFinding(
        total_messages=5,
        avg_length_chars=42.0,
        style_profile=_style_profile(),
        debug_commit_ratio=0.1,
        self_baseline_divergence=0.25,
    )
    assert finding.total_messages == 5
    assert finding.avg_length_chars == 42.0
    assert finding.debug_commit_ratio == 0.1


# ---------------------------------------------------------------------------
# AuthorIdentityFinding
# ---------------------------------------------------------------------------


def test_author_identity_finding_minimal() -> None:
    finding = AuthorIdentityFinding(
        unique_author_emails=["a@example.com"],
        unique_author_names=["Alice"],
        drift_detected=False,
    )
    assert finding.unique_author_emails == ["a@example.com"]
    assert finding.drift_detected is False


# ---------------------------------------------------------------------------
# FileEvolutionFinding
# ---------------------------------------------------------------------------


def test_file_evolution_finding_minimal() -> None:
    finding = FileEvolutionFinding(
        file_path="byline/models.py",
        total_commits_touching=3,
        largest_single_addition_lines=400,
        appears_pasted=True,
    )
    assert finding.file_path == "byline/models.py"
    assert finding.appears_pasted is True


# ---------------------------------------------------------------------------
# HistoryFindings
# ---------------------------------------------------------------------------


def _commit_timeline() -> CommitTimelineFinding:
    return CommitTimelineFinding(
        total_commits=1,
        span_seconds=0.0,
        burst_density=0.0,
        burst_window_start=None,
        bursty=False,
        first_commit_file_count=1,
        first_commit_loc=1,
        first_commit_appears_pasted=False,
    )


def _commit_messages() -> CommitMessageStyleFinding:
    return CommitMessageStyleFinding(
        total_messages=1,
        avg_length_chars=10.0,
        style_profile=_style_profile(),
        debug_commit_ratio=0.0,
        self_baseline_divergence=0.0,
    )


def _author_identity() -> AuthorIdentityFinding:
    return AuthorIdentityFinding(
        unique_author_emails=["x@y.com"],
        unique_author_names=["x"],
        drift_detected=False,
    )


def test_history_findings_minimal() -> None:
    findings = HistoryFindings(
        timeline=_commit_timeline(),
        messages=_commit_messages(),
        identity=_author_identity(),
        file_evolutions=[],
    )
    assert findings.timeline.total_commits == 1
    assert findings.file_evolutions == []


# ---------------------------------------------------------------------------
# AlignmentCheck
# ---------------------------------------------------------------------------


def test_alignment_check_minimal() -> None:
    check = AlignmentCheck(
        kind="cli_flag_documented_missing_in_code",
        source="deterministic",
        description="--foo flag in README not present in CLI.",
        doc_location="README.md:10",
        code_location=None,
        severity="notable",
    )
    assert check.kind == "cli_flag_documented_missing_in_code"
    assert check.source == "deterministic"
    assert check.severity == "notable"


def test_alignment_check_invalid_kind_raises() -> None:
    with pytest.raises(ValidationError):
        AlignmentCheck(
            kind="not_a_valid_kind",  # type: ignore[arg-type]
            source="deterministic",
            description="d",
            doc_location=None,
            code_location=None,
            severity="info",
        )


def test_alignment_check_invalid_source_raises() -> None:
    with pytest.raises(ValidationError):
        AlignmentCheck(
            kind="cli_flag_documented_missing_in_code",
            source="manual",  # type: ignore[arg-type]
            description="d",
            doc_location=None,
            code_location=None,
            severity="info",
        )


def test_alignment_check_invalid_severity_raises() -> None:
    with pytest.raises(ValidationError):
        AlignmentCheck(
            kind="cli_flag_documented_missing_in_code",
            source="deterministic",
            description="d",
            doc_location=None,
            code_location=None,
            severity="critical",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# AlignmentFindings
# ---------------------------------------------------------------------------


def test_alignment_findings_minimal() -> None:
    findings = AlignmentFindings(
        checks=[],
        deterministic_only=True,
        overall_alignment="aligned",
        llm_summary=None,
    )
    assert findings.deterministic_only is True
    assert findings.overall_alignment == "aligned"


def test_alignment_findings_invalid_overall_alignment_raises() -> None:
    with pytest.raises(ValidationError):
        AlignmentFindings(
            checks=[],
            deterministic_only=True,
            overall_alignment="totally_misaligned",  # type: ignore[arg-type]
            llm_summary=None,
        )


# ---------------------------------------------------------------------------
# VoiceFinding
# ---------------------------------------------------------------------------


def test_voice_finding_minimal() -> None:
    finding = VoiceFinding(
        first_person_count=4,
        first_person_per_1k_words=1.5,
        has_first_person_voice=True,
        ai_disclosure_found=False,
        ai_disclosure_file=None,
        ai_disclosure_excerpt=None,
    )
    assert finding.first_person_count == 4
    assert finding.has_first_person_voice is True
    assert finding.ai_disclosure_file is None


# ---------------------------------------------------------------------------
# BoilerplateFinding
# ---------------------------------------------------------------------------


def test_boilerplate_finding_minimal() -> None:
    finding = BoilerplateFinding(
        meta_files_present=["README.md", "LICENSE"],
        meta_files_checked=["README.md", "LICENSE", ".gitignore"],
        density_ratio=0.67,
        severity="normal",
    )
    assert finding.density_ratio == 0.67
    assert finding.severity == "normal"


def test_boilerplate_finding_invalid_severity_raises() -> None:
    with pytest.raises(ValidationError):
        BoilerplateFinding(
            meta_files_present=[],
            meta_files_checked=[],
            density_ratio=0.0,
            severity="critical",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# SelfBaselineFinding
# ---------------------------------------------------------------------------


def test_self_baseline_finding_minimal() -> None:
    finding = SelfBaselineFinding(
        commit_msg_vs_readme_distance=0.2,
        code_comment_vs_readme_distance=0.15,
        within_repo_divergence="consistent",
        note="No notable divergence.",
    )
    assert finding.within_repo_divergence == "consistent"
    assert finding.note == "No notable divergence."


def test_self_baseline_finding_invalid_divergence_raises() -> None:
    with pytest.raises(ValidationError):
        SelfBaselineFinding(
            commit_msg_vs_readme_distance=0.0,
            code_comment_vs_readme_distance=0.0,
            within_repo_divergence="huge",  # type: ignore[arg-type]
            note="",
        )


# ---------------------------------------------------------------------------
# Question
# ---------------------------------------------------------------------------


def test_question_minimal() -> None:
    q = Question(
        text="Why did you choose pydantic v2?",
        grounding_file="byline/models.py",
        grounding_line=8,
        signal_addressed="dependency_choice",
        rationale="To check the candidate's awareness of v2 changes.",
    )
    assert q.text.startswith("Why")
    assert q.grounding_file == "byline/models.py"
    assert q.grounding_line == 8


def test_question_allows_none_grounding() -> None:
    q = Question(
        text="t",
        grounding_file=None,
        grounding_line=None,
        signal_addressed="x",
        rationale="r",
    )
    assert q.grounding_file is None
    assert q.grounding_line is None


# ---------------------------------------------------------------------------
# QuestionSet
# ---------------------------------------------------------------------------


def test_question_set_minimal() -> None:
    now = datetime.now(timezone.utc)
    qs = QuestionSet(
        questions=[
            Question(
                text="t",
                grounding_file=None,
                grounding_line=None,
                signal_addressed="x",
                rationale="r",
            )
        ],
        generated_at=now,
    )
    assert len(qs.questions) == 1
    assert qs.generated_at == now


# ---------------------------------------------------------------------------
# AuditResult v0.2 backwards compatibility & extended fields
# ---------------------------------------------------------------------------


def test_audit_result_backwards_compat_no_new_fields() -> None:
    """AuditResult must still construct with only v0.1 fields supplied."""
    result = AuditResult(
        target_repo="r",
        candidate=None,
        baseline=None,
        target_profile=_style_profile(),
        fingerprints=[],
        disproportions=[],
        deltas=[],
        llm_qualitative=None,
        overall_signal="aligned",
    )
    assert result.history is None
    assert result.alignment is None
    assert result.voice is None
    assert result.boilerplate is None
    assert result.self_baseline is None


def test_audit_result_with_v02_fields_populated() -> None:
    history = HistoryFindings(
        timeline=_commit_timeline(),
        messages=_commit_messages(),
        identity=_author_identity(),
        file_evolutions=[],
    )
    alignment = AlignmentFindings(
        checks=[
            AlignmentCheck(
                kind="env_var_documented_missing_in_code",
                source="llm",
                description="d",
                doc_location="README.md",
                code_location=None,
                severity="info",
            )
        ],
        deterministic_only=False,
        overall_alignment="minor_gaps",
        llm_summary="Looks mostly aligned.",
    )
    voice = VoiceFinding(
        first_person_count=0,
        first_person_per_1k_words=0.0,
        has_first_person_voice=False,
        ai_disclosure_found=True,
        ai_disclosure_file="README.md",
        ai_disclosure_excerpt="Built with assistance from an LLM.",
    )
    boilerplate = BoilerplateFinding(
        meta_files_present=["README.md"],
        meta_files_checked=["README.md", "LICENSE"],
        density_ratio=0.5,
        severity="notable",
    )
    self_baseline = SelfBaselineFinding(
        commit_msg_vs_readme_distance=0.4,
        code_comment_vs_readme_distance=0.3,
        within_repo_divergence="notable",
        note="Commit messages diverge from README voice.",
    )
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
        history=history,
        alignment=alignment,
        voice=voice,
        boilerplate=boilerplate,
        self_baseline=self_baseline,
    )
    assert result.history is history
    assert result.alignment.overall_alignment == "minor_gaps"
    assert result.voice.ai_disclosure_found is True
    assert result.boilerplate.severity == "notable"
    assert result.self_baseline.within_repo_divergence == "notable"
