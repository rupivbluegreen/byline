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


class CommitTimelineFinding(BaseModel):
    """Timeline signals from a repo's commit history."""

    total_commits: int
    span_seconds: float
    burst_density: float
    burst_window_start: datetime | None
    bursty: bool
    first_commit_file_count: int
    first_commit_loc: int
    first_commit_appears_pasted: bool


class CommitMessageStyleFinding(BaseModel):
    """Style profile of commit messages vs. within-repo self-baseline."""

    total_messages: int
    avg_length_chars: float
    style_profile: StyleProfile
    debug_commit_ratio: float
    self_baseline_divergence: float


class AuthorIdentityFinding(BaseModel):
    """Author email/name spread across the commit history."""

    unique_author_emails: list[str]
    unique_author_names: list[str]
    drift_detected: bool


class FileEvolutionFinding(BaseModel):
    """How a single file grew across the commit history."""

    file_path: str
    total_commits_touching: int
    largest_single_addition_lines: int
    appears_pasted: bool


class HistoryFindings(BaseModel):
    """Combined commit-history forensics output."""

    timeline: CommitTimelineFinding
    messages: CommitMessageStyleFinding
    identity: AuthorIdentityFinding
    file_evolutions: list[FileEvolutionFinding]


class AlignmentCheck(BaseModel):
    """A single alignment check between documentation and code."""

    kind: Literal[
        "cli_flag_documented_missing_in_code",
        "env_var_documented_missing_in_code",
        "command_documented_missing_file",
        "dependency_documented_missing_in_manifest",
        "doc_claims_feature_not_in_code",
        "code_behavior_not_documented",
        "config_documented_but_unused",
        "command_documented_but_missing",
    ]
    source: Literal["deterministic", "llm"]
    description: str
    doc_location: str | None
    code_location: str | None
    severity: Literal["info", "notable", "significant"]


class AlignmentFindings(BaseModel):
    """Result of running alignment checks (deterministic and/or semantic)."""

    checks: list[AlignmentCheck]
    deterministic_only: bool
    overall_alignment: Literal["aligned", "minor_gaps", "significant_gaps"]
    llm_summary: str | None


class VoiceFinding(BaseModel):
    """First-person voice presence and AI-use disclosure signals."""

    first_person_count: int
    first_person_per_1k_words: float
    has_first_person_voice: bool
    ai_disclosure_found: bool
    ai_disclosure_file: str | None
    ai_disclosure_excerpt: str | None


class BoilerplateFinding(BaseModel):
    """Density of standard meta-files commonly present in real projects."""

    meta_files_present: list[str]
    meta_files_checked: list[str]
    density_ratio: float
    severity: Literal["normal", "notable", "significant"]


class SelfBaselineFinding(BaseModel):
    """Within-repo divergence between commit messages, README, and code comments."""

    commit_msg_vs_readme_distance: float
    code_comment_vs_readme_distance: float
    within_repo_divergence: Literal["consistent", "notable", "significant"]
    note: str


class Question(BaseModel):
    """A single interview question grounded in a specific file/line."""

    text: str
    grounding_file: str | None
    grounding_line: int | None
    signal_addressed: str
    rationale: str


class QuestionSet(BaseModel):
    """A generated set of interview questions for a candidate submission."""

    questions: list[Question]
    generated_at: datetime


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
    history: HistoryFindings | None = None
    alignment: AlignmentFindings | None = None
    voice: VoiceFinding | None = None
    boilerplate: BoilerplateFinding | None = None
    self_baseline: SelfBaselineFinding | None = None
