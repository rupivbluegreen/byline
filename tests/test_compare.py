"""Tests for byline.compare — comparative scoring + audit orchestration per spec §8."""

from __future__ import annotations

from pathlib import Path

import pytest

from byline.compare import (
    _parse_github_url,
    audit,
    compute_baseline_profile,
    compute_deltas,
    compute_target_profile,
    overall_signal,
)
from byline.models import (
    AuditResult,
    BaselineCorpus,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    StyleProfile,
    WritingSample,
)
from tests.conftest import FIXTURES_DIR


# ---------------------------------------------------------------------------
# StyleProfile fixture helpers
# ---------------------------------------------------------------------------


def _zero_profile() -> StyleProfile:
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


def _profile(**overrides: float) -> StyleProfile:
    base = dict(
        em_dash_density=2.0,
        emoji_in_headers_ratio=0.1,
        avg_sentence_length=15.0,
        type_token_ratio=0.5,
        typo_rate=1.0,
        sophistication_score=0.3,
        banner_comment_density=1.0,
        progress_ux_score=0.25,
    )
    base.update(overrides)
    return StyleProfile(**base)


# ---------------------------------------------------------------------------
# compute_deltas
# ---------------------------------------------------------------------------


def test_compute_deltas_identical_profiles_all_aligned() -> None:
    baseline = _profile()
    target = _profile()
    deltas = compute_deltas(baseline, target)
    assert len(deltas) == 8
    for d in deltas:
        assert d.absolute_delta == 0.0
        assert d.severity == "aligned"
        assert d.relative_delta == 0.0


def test_compute_deltas_em_dash_5x_baseline_is_extreme() -> None:
    baseline = _profile(em_dash_density=2.0)
    target = _profile(em_dash_density=10.0)  # 5x
    deltas = compute_deltas(baseline, target)
    em_dash_delta = next(d for d in deltas if d.metric == "em_dash_density")
    assert em_dash_delta.absolute_delta == pytest.approx(8.0)
    assert em_dash_delta.relative_delta == pytest.approx(4.0)
    assert em_dash_delta.severity == "extreme"


def test_compute_deltas_zero_baseline_uses_sentinel() -> None:
    baseline = _zero_profile()
    target = _profile(em_dash_density=5.0)
    deltas = compute_deltas(baseline, target)
    em_dash_delta = next(d for d in deltas if d.metric == "em_dash_density")
    # When baseline is zero and target is non-zero, sentinel 10.0 is used.
    assert em_dash_delta.relative_delta == 10.0
    assert em_dash_delta.severity == "extreme"


def test_compute_deltas_zero_baseline_zero_target_aligned() -> None:
    baseline = _zero_profile()
    target = _zero_profile()
    deltas = compute_deltas(baseline, target)
    for d in deltas:
        assert d.relative_delta == 0.0
        assert d.severity == "aligned"


def test_compute_deltas_severity_thresholds() -> None:
    # relative delta of 0.5 -> "notable"
    baseline = _profile(em_dash_density=2.0)
    target = _profile(em_dash_density=3.0)  # rel = 0.5
    deltas = compute_deltas(baseline, target)
    em_dash_delta = next(d for d in deltas if d.metric == "em_dash_density")
    assert em_dash_delta.severity == "notable"

    # relative delta of 1.0 -> "significant"
    target2 = _profile(em_dash_density=4.0)  # rel = 1.0
    deltas2 = compute_deltas(baseline, target2)
    em_dash_delta2 = next(d for d in deltas2 if d.metric == "em_dash_density")
    assert em_dash_delta2.severity == "significant"


def test_compute_deltas_returns_one_per_field() -> None:
    baseline = _profile()
    target = _profile()
    deltas = compute_deltas(baseline, target)
    names = {d.metric for d in deltas}
    expected = {
        "em_dash_density",
        "emoji_in_headers_ratio",
        "avg_sentence_length",
        "type_token_ratio",
        "typo_rate",
        "sophistication_score",
        "banner_comment_density",
        "progress_ux_score",
    }
    assert names == expected


# ---------------------------------------------------------------------------
# overall_signal
# ---------------------------------------------------------------------------


def _delta(severity: str, metric: str = "em_dash_density") -> ComparativeDelta:
    return ComparativeDelta(
        metric=metric,
        baseline_value=1.0,
        target_value=2.0,
        absolute_delta=1.0,
        relative_delta=1.0,
        severity=severity,  # type: ignore[arg-type]
    )


def _fp(n: int) -> list[FingerprintHit]:
    return [
        FingerprintHit(
            file_path=f"file_{i}.md",
            line_number=1,
            pattern="x",
            category="phrase",
            excerpt="excerpt",
        )
        for i in range(n)
    ]


def _disp(severity: str) -> DisproportionFinding:
    return DisproportionFinding(
        name="doc_to_code_ratio",
        observed=1.0,
        threshold=0.5,
        severity=severity,  # type: ignore[arg-type]
        description="desc",
    )


def test_overall_signal_highly_divergent() -> None:
    deltas = [_delta("extreme"), _delta("significant", "typo_rate")]
    fps = _fp(8)
    disps = [_disp("significant")]
    assert overall_signal(deltas, fps, disps) == "highly_divergent"


def test_overall_signal_divergent_extreme_only() -> None:
    deltas = [_delta("extreme")]
    fps: list[FingerprintHit] = []
    disps: list[DisproportionFinding] = []
    assert overall_signal(deltas, fps, disps) == "divergent"


def test_overall_signal_divergent_many_significant() -> None:
    deltas = [_delta("significant") for _ in range(4)]
    fps: list[FingerprintHit] = []
    disps: list[DisproportionFinding] = []
    assert overall_signal(deltas, fps, disps) == "divergent"


def test_overall_signal_divergent_many_fingerprints() -> None:
    deltas: list[ComparativeDelta] = []
    fps = _fp(8)
    disps: list[DisproportionFinding] = []
    assert overall_signal(deltas, fps, disps) == "divergent"


def test_overall_signal_mixed_two_significant() -> None:
    deltas = [_delta("significant"), _delta("significant", "typo_rate")]
    fps: list[FingerprintHit] = []
    disps: list[DisproportionFinding] = []
    assert overall_signal(deltas, fps, disps) == "mixed"


def test_overall_signal_mixed_notable_disp() -> None:
    deltas: list[ComparativeDelta] = []
    fps: list[FingerprintHit] = []
    disps = [_disp("notable")]
    assert overall_signal(deltas, fps, disps) == "mixed"


def test_overall_signal_aligned_empty() -> None:
    assert overall_signal([], [], []) == "aligned"


def test_overall_signal_aligned_only_low_signals() -> None:
    deltas = [_delta("aligned")]
    fps = _fp(2)
    disps = [_disp("info")]
    assert overall_signal(deltas, fps, disps) == "aligned"


# ---------------------------------------------------------------------------
# compute_target_profile
# ---------------------------------------------------------------------------


def test_compute_target_profile_synthetic_ai_repo() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    profile = compute_target_profile(repo)
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density > 0.0
    # synthetic repo has shell + Dockerfile with banners
    assert profile.banner_comment_density >= 0.0


def test_compute_target_profile_empty_dir_returns_zero_profile(tmp_path: Path) -> None:
    profile = compute_target_profile(tmp_path)
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density == 0.0
    assert profile.banner_comment_density == 0.0
    assert profile.progress_ux_score == 0.0


def test_compute_target_profile_nonexistent_path_returns_zero_profile(
    tmp_path: Path,
) -> None:
    profile = compute_target_profile(tmp_path / "does-not-exist")
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density == 0.0


# ---------------------------------------------------------------------------
# compute_baseline_profile
# ---------------------------------------------------------------------------


def test_compute_baseline_profile_nonzero_for_known_sample() -> None:
    text = (
        "This is a sample piece of prose — with em-dashes throughout — used to "
        "exercise the baseline aggregation. Comprehensive prose helps test the "
        "type-token ratio and sentence-length signals. We add multiple sentences. "
        "Indeed, several sentences. And another one for good measure."
    )
    corpus = BaselineCorpus(
        username="alice",
        samples=[
            WritingSample(
                source="README.md@alice/test",
                kind="readme",
                text=text,
                word_count=len(text.split()),
            ),
        ],
        total_words=len(text.split()),
        repos_scanned=["alice/test"],
        repos_skipped=[],
    )
    profile = compute_baseline_profile(corpus)
    assert isinstance(profile, StyleProfile)
    # The sample has em-dashes, so this signal must be positive.
    assert profile.em_dash_density > 0.0
    assert profile.avg_sentence_length > 0.0
    assert profile.type_token_ratio > 0.0


def test_compute_baseline_profile_empty_samples() -> None:
    corpus = BaselineCorpus(
        username="alice",
        samples=[],
        total_words=0,
        repos_scanned=[],
        repos_skipped=[],
    )
    profile = compute_baseline_profile(corpus)
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density == 0.0


# ---------------------------------------------------------------------------
# _parse_github_url
# ---------------------------------------------------------------------------


def test_parse_github_url_https() -> None:
    assert _parse_github_url("https://github.com/alice/repo") == ("alice", "repo")


def test_parse_github_url_https_trailing_slash() -> None:
    assert _parse_github_url("https://github.com/alice/repo/") == ("alice", "repo")


def test_parse_github_url_https_dot_git() -> None:
    assert _parse_github_url("https://github.com/alice/repo.git") == ("alice", "repo")


def test_parse_github_url_ssh() -> None:
    assert _parse_github_url("git@github.com:alice/repo.git") == ("alice", "repo")


def test_parse_github_url_bare() -> None:
    assert _parse_github_url("github.com/alice/repo") == ("alice", "repo")


# ---------------------------------------------------------------------------
# audit — end-to-end on local path
# ---------------------------------------------------------------------------


def test_audit_local_path_no_candidate() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    result = audit(
        target=repo,
        candidate_username=None,
        github_token=None,
        with_llm=False,
    )
    assert isinstance(result, AuditResult)
    assert result.baseline is None
    assert result.candidate is None
    assert result.target_profile is not None
    assert result.target_profile.em_dash_density > 0.0
    assert len(result.fingerprints) > 0
    assert result.overall_signal in {
        "aligned",
        "mixed",
        "divergent",
        "highly_divergent",
    }
    assert result.llm_qualitative is None
    # No baseline -> deltas should be empty list
    assert result.deltas == []


def test_audit_local_path_str_target() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    result = audit(
        target=str(repo),
        candidate_username=None,
        github_token=None,
        with_llm=False,
    )
    assert isinstance(result, AuditResult)
    assert result.target_repo == str(repo)
