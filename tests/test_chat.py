"""Tests for byline.chat — interactive REPL helpers and command parsing.

The REPL loop itself is interactive and not tested here. Tests focus on
``parse_command`` and helper renderers, plus the upfront LLM-required guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from byline.chat import (
    CHAT_HELP_TEXT,
    MAX_HISTORY_TURNS,
    parse_command,
    render_audit_summary,
    render_findings,
    run_chat_session,
)
from byline.llm import LLMUnavailableError
from byline.models import (
    AuditResult,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    StyleProfile,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_style_profile() -> StyleProfile:
    return StyleProfile(
        em_dash_density=0.0,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=10.0,
        type_token_ratio=0.5,
        typo_rate=0.0,
        sophistication_score=0.3,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )


def _make_audit(
    *,
    candidate: str | None = "alice",
    fingerprints: list[FingerprintHit] | None = None,
    disproportions: list[DisproportionFinding] | None = None,
    deltas: list[ComparativeDelta] | None = None,
) -> AuditResult:
    return AuditResult(
        target_repo="/tmp/x",
        candidate=candidate,
        baseline=None,
        target_profile=_make_style_profile(),
        fingerprints=fingerprints or [],
        disproportions=disproportions or [],
        deltas=deltas or [],
        llm_qualitative=None,
        overall_signal="aligned",
    )


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


def test_parse_command_help():
    assert parse_command("/help") == ("help", "")


def test_parse_command_show_with_args():
    assert parse_command("/show README.md") == ("show", "README.md")


def test_parse_command_plain_text_is_none_command():
    assert parse_command("plain text here") == (None, "plain text here")


def test_parse_command_empty_string():
    assert parse_command("") == (None, "")


def test_parse_command_whitespace_only():
    assert parse_command("   ") == (None, "")


def test_parse_command_case_insensitive():
    assert parse_command("/QUIT") == ("quit", "")


def test_parse_command_case_insensitive_with_args():
    assert parse_command("/SHOW main.py") == ("show", "main.py")


def test_parse_command_strips_surrounding_whitespace():
    assert parse_command("   /help   ") == ("help", "")


def test_parse_command_show_with_multiword_args():
    cmd, rest = parse_command("/save some path with spaces.md")
    assert cmd == "save"
    assert rest == "some path with spaces.md"


def test_parse_command_just_slash():
    # Bare "/" → empty command, empty rest
    cmd, rest = parse_command("/")
    assert cmd == ""
    assert rest == ""


# ---------------------------------------------------------------------------
# render_audit_summary
# ---------------------------------------------------------------------------


def test_render_audit_summary_contains_key_fields():
    audit = _make_audit()
    summary = render_audit_summary(audit)
    assert "/tmp/x" in summary  # target_repo
    assert "alice" in summary  # candidate
    assert "aligned" in summary  # overall_signal
    # Spec says 4-line summary; tolerate extra detail lines but require ≥4
    assert len(summary.splitlines()) >= 4


def test_render_audit_summary_handles_missing_candidate():
    audit = _make_audit(candidate=None)
    summary = render_audit_summary(audit)
    assert "not provided" in summary.lower()


def test_render_audit_summary_includes_counts():
    fp = FingerprintHit(
        file_path="a.py",
        line_number=1,
        pattern="banner",
        category="shell_banner",
        excerpt="### header ###",
    )
    delta = ComparativeDelta(
        metric="em_dash_density",
        baseline_value=0.1,
        target_value=0.5,
        absolute_delta=0.4,
        relative_delta=4.0,
        severity="significant",
    )
    audit = _make_audit(fingerprints=[fp], deltas=[delta])
    summary = render_audit_summary(audit)
    # Should mention 1 fingerprint and 1 delta count somewhere
    assert "1" in summary


# ---------------------------------------------------------------------------
# render_findings
# ---------------------------------------------------------------------------


def test_render_findings_mentions_counts():
    fp = FingerprintHit(
        file_path="a.py",
        line_number=10,
        pattern="banner_comment",
        category="shell_banner",
        excerpt="### Section ###",
    )
    delta = ComparativeDelta(
        metric="em_dash_density",
        baseline_value=0.1,
        target_value=0.6,
        absolute_delta=0.5,
        relative_delta=5.0,
        severity="significant",
    )
    audit = _make_audit(fingerprints=[fp], deltas=[delta])
    out = render_findings(audit)
    assert "fingerprint" in out.lower()
    assert "delta" in out.lower()


def test_render_findings_empty_audit_still_returns_string():
    audit = _make_audit()
    out = render_findings(audit)
    assert isinstance(out, str)
    assert len(out) > 0


def test_render_findings_caps_at_30_lines():
    fps = [
        FingerprintHit(
            file_path=f"f{i}.py",
            line_number=i,
            pattern=f"pat{i}",
            category="phrase",
            excerpt=f"excerpt {i}",
        )
        for i in range(100)
    ]
    audit = _make_audit(fingerprints=fps)
    out = render_findings(audit)
    assert len(out.splitlines()) <= 35  # generous bound; spec says ~30


# ---------------------------------------------------------------------------
# run_chat_session — only the LLM-required guard is testable headlessly
# ---------------------------------------------------------------------------


def test_run_chat_session_requires_provider(tmp_path: Path):
    audit = _make_audit()
    with pytest.raises(LLMUnavailableError):
        run_chat_session(audit, repo_path=tmp_path, provider=None)


def test_run_chat_session_rejects_legacy_anthropic_client_none(tmp_path: Path):
    """The deprecated ``anthropic_client=None`` keyword still raises."""
    audit = _make_audit()
    with pytest.raises(LLMUnavailableError):
        run_chat_session(audit, repo_path=tmp_path, anthropic_client=None)


# ---------------------------------------------------------------------------
# Module constants present
# ---------------------------------------------------------------------------


def test_chat_help_text_lists_commands():
    assert "/quit" in CHAT_HELP_TEXT
    assert "/help" in CHAT_HELP_TEXT
    assert "/summary" in CHAT_HELP_TEXT
    assert "/show" in CHAT_HELP_TEXT
    assert "/findings" in CHAT_HELP_TEXT
    assert "/questions" in CHAT_HELP_TEXT
    assert "/save" in CHAT_HELP_TEXT
    assert "/reset" in CHAT_HELP_TEXT


def test_max_history_turns_is_a_positive_int():
    assert isinstance(MAX_HISTORY_TURNS, int)
    assert MAX_HISTORY_TURNS > 0
