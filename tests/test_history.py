"""Tests for the commit-history forensics module (`byline.history`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from byline.history import (
    analyze_commit_messages,
    analyze_file_evolution,
    analyze_identity,
    analyze_timeline,
    audit_history,
)
from byline.models import (
    AuthorIdentityFinding,
    CommitMessageStyleFinding,
    CommitTimelineFinding,
    FileEvolutionFinding,
    HistoryFindings,
)


# ---------------------------------------------------------------------------
# analyze_timeline
# ---------------------------------------------------------------------------


def test_analyze_timeline_pasted_repo_flags_first_commit(pasted_repo: Path) -> None:
    finding = analyze_timeline(pasted_repo)
    assert isinstance(finding, CommitTimelineFinding)
    assert finding.total_commits == 1
    assert finding.first_commit_appears_pasted is True


def test_analyze_timeline_iterated_repo_is_not_bursty_or_pasted(iterated_repo: Path) -> None:
    finding = analyze_timeline(iterated_repo)
    assert finding.total_commits == 20
    assert finding.first_commit_appears_pasted is False
    assert finding.bursty is False


def test_analyze_timeline_bursty_repo_flags_burst(bursty_repo: Path) -> None:
    finding = analyze_timeline(bursty_repo)
    assert finding.total_commits == 10
    assert finding.bursty is True
    assert finding.burst_density > 0.7
    assert finding.burst_window_start is not None


# ---------------------------------------------------------------------------
# analyze_identity
# ---------------------------------------------------------------------------


def test_analyze_identity_drifted_repo(drifted_identity_repo: Path) -> None:
    finding = analyze_identity(drifted_identity_repo)
    assert isinstance(finding, AuthorIdentityFinding)
    assert finding.drift_detected is True
    assert len(finding.unique_author_emails) >= 2
    # Emails should be lowercased and sorted.
    assert finding.unique_author_emails == sorted(finding.unique_author_emails)


def test_analyze_identity_single_author(iterated_repo: Path) -> None:
    finding = analyze_identity(iterated_repo)
    assert finding.drift_detected is False
    assert len(finding.unique_author_emails) == 1


# ---------------------------------------------------------------------------
# analyze_commit_messages
# ---------------------------------------------------------------------------


def test_analyze_commit_messages_iterated_repo(iterated_repo: Path) -> None:
    finding = analyze_commit_messages(iterated_repo)
    assert isinstance(finding, CommitMessageStyleFinding)
    assert finding.total_messages == 20
    # Iterated repo uses clean messages like "add file 02" — no debug markers.
    assert finding.debug_commit_ratio == 0.0


def test_analyze_commit_messages_debug_repo(debug_message_repo: Path) -> None:
    finding = analyze_commit_messages(debug_message_repo)
    # "fix typo" + "wip" land two debug markers in three messages.
    assert finding.debug_commit_ratio > 0.0


# ---------------------------------------------------------------------------
# analyze_file_evolution
# ---------------------------------------------------------------------------


def test_analyze_file_evolution_pasted_readme(pasted_repo: Path) -> None:
    finding = analyze_file_evolution(pasted_repo, "README.md")
    assert isinstance(finding, FileEvolutionFinding)
    assert finding.total_commits_touching == 1
    assert finding.largest_single_addition_lines > 0
    assert finding.appears_pasted is True


def test_analyze_file_evolution_missing_file(iterated_repo: Path) -> None:
    finding = analyze_file_evolution(iterated_repo, "definitely_not_here.xyz")
    assert finding.total_commits_touching == 0
    assert finding.largest_single_addition_lines == 0
    assert finding.appears_pasted is False


# ---------------------------------------------------------------------------
# audit_history
# ---------------------------------------------------------------------------


def test_audit_history_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    (plain / "README.md").write_text("hello\n", encoding="utf-8")

    findings = audit_history(plain)
    assert isinstance(findings, HistoryFindings)
    assert findings.timeline.total_commits == 0
    assert findings.messages.total_messages == 0
    assert findings.identity.unique_author_emails == []
    assert findings.identity.drift_detected is False
    assert findings.file_evolutions == []


def test_audit_history_iterated_repo(iterated_repo: Path) -> None:
    findings = audit_history(iterated_repo)
    assert findings.timeline.total_commits == 20
    assert findings.identity.drift_detected is False
    assert findings.messages.total_messages == 20
    # README.md exists in the iterated repo, so it should appear in file_evolutions.
    paths = [fe.file_path for fe in findings.file_evolutions]
    assert "README.md" in paths


def test_audit_history_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    findings = audit_history(missing)
    assert isinstance(findings, HistoryFindings)
    assert findings.timeline.total_commits == 0
    assert findings.file_evolutions == []
