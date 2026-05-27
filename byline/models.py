"""Pydantic data models for submissions, baselines, metrics, and reports. See spec §5."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class WritingSample(BaseModel):
    """A single piece of human-authored prose used as a comparative-signal data point."""

    source: str  # e.g. "README.md@username/repo"
    kind: Literal["readme", "commit_message", "comment", "issue_body"]
    text: str
    word_count: int


class BaselineCorpus(BaseModel):
    """Aggregated baseline of a candidate's prior writing used for comparative analysis."""

    username: str
    samples: list[WritingSample]
    total_words: int
    repos_scanned: list[str]
    repos_skipped: list[tuple[str, str]]  # (repo_name, reason)


class MetricResult(BaseModel):
    """A single numeric stylistic signal computed over a corpus or target."""

    name: str
    value: float
    unit: str  # e.g. "per 1000 words"
    description: str


class StyleProfile(BaseModel):
    """Bundle of stylistic signals describing the writing surface of a corpus or target."""

    em_dash_density: float
    emoji_in_headers_ratio: float
    avg_sentence_length: float
    type_token_ratio: float
    typo_rate: float
    sophistication_score: float
    banner_comment_density: float
    progress_ux_score: float


class FingerprintHit(BaseModel):
    """A located occurrence of a known stylistic pattern of interest in the target."""

    file_path: str
    line_number: int | None
    pattern: str
    category: Literal["phrase", "structure", "shell_banner", "progress_ux", "ai_section_header"]
    excerpt: str


class DisproportionFinding(BaseModel):
    """A structural ratio in the target that diverges from typical project proportions."""

    name: str
    observed: float
    threshold: float
    severity: Literal["info", "notable", "significant"]
    description: str


class ComparativeDelta(BaseModel):
    """The gap between a baseline signal value and the target's value for the same metric."""

    metric: str
    baseline_value: float
    target_value: float
    absolute_delta: float
    relative_delta: float
    severity: Literal["aligned", "notable", "significant", "extreme"]


class AuditResult(BaseModel):
    """Full comparative analysis output: signals, divergences, and an overall alignment summary."""

    target_repo: str
    candidate: str | None
    baseline: BaselineCorpus | None
    target_profile: StyleProfile
    fingerprints: list[FingerprintHit]
    disproportions: list[DisproportionFinding]
    deltas: list[ComparativeDelta]
    llm_qualitative: str | None
    overall_signal: Literal["aligned", "mixed", "divergent", "highly_divergent"]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
