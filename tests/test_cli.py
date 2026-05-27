"""Tests for the Typer CLI surface (`byline.cli`). See spec §4.

These tests exercise the CLI end-to-end via :class:`typer.testing.CliRunner`.
We deliberately avoid touching the network: the `scan` smoke test runs
against the on-disk synthetic fixture, and the `baseline` test patches the
GitHub-backed corpus builder.

The framing-language assertion is load-bearing: the CLI must talk about
*comparative* signals and *divergence*, never about "AI detection". A
regression there would silently change the product's positioning.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from byline import __version__
from byline.cli import app
from byline.models import (
    AuditResult,
    BaselineCorpus,
    StyleProfile,
    WritingSample,
)

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    """Strip ANSI escapes and collapse whitespace so substring assertions
    survive Rich's pretty-printing wraps in non-TTY CI runners."""
    stripped = _ANSI_RE.sub("", text)
    return re.sub(r"\s+", " ", stripped)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_REPO = FIXTURES_DIR / "synthetic_ai_repo"


# ---------------------------------------------------------------------------
# Help and version
# ---------------------------------------------------------------------------


def test_help_lists_all_commands() -> None:
    """`byline --help` lists the three commands."""

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "audit" in result.stdout
    assert "baseline" in result.stdout
    assert "scan" in result.stdout


def test_help_uses_comparative_framing_not_detector() -> None:
    """The root help text frames the tool comparatively, never as a detector."""

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    lower = result.stdout.lower()
    assert "comparative" in lower
    assert "ai detector" not in lower
    assert "detect ai" not in lower


def test_version_flag_prints_version_and_exits() -> None:
    """`byline --version` prints the package version and exits 0."""

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert __version__ in result.stdout
    assert "byline" in result.stdout


# ---------------------------------------------------------------------------
# scan (uses the synthetic fixture; no network)
# ---------------------------------------------------------------------------


def test_scan_synthetic_repo_emits_markdown_report() -> None:
    """`byline scan <fixture>` writes the canonical Markdown sections."""

    result = runner.invoke(
        app,
        ["scan", str(SYNTHETIC_REPO), "--no-color"],
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "Comparative Attribution Report" in result.stdout
    assert "Fingerprint findings" in result.stdout


def test_scan_json_flag_outputs_valid_json() -> None:
    """`--json` switches output to a parseable JSON document."""

    result = runner.invoke(
        app,
        ["scan", str(SYNTHETIC_REPO), "--json"],
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["target_repo"] == str(SYNTHETIC_REPO)
    assert payload["candidate"] is None
    assert "fingerprints" in payload
    assert "disproportions" in payload


def test_scan_output_flag_writes_markdown_to_file(tmp_path: Path) -> None:
    """`--output` redirects the Markdown report to a file on disk."""

    out_file = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["scan", str(SYNTHETIC_REPO), "--output", str(out_file)],
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "Comparative" in text


def test_scan_json_and_docx_are_incompatible(tmp_path: Path) -> None:
    """Combining `--json` with `--docx` fails fast with exit code 3."""

    docx_file = tmp_path / "report.docx"
    result = runner.invoke(
        app,
        [
            "scan",
            str(SYNTHETIC_REPO),
            "--json",
            "--docx",
            str(docx_file),
        ],
    )

    assert result.exit_code == 3
    assert "incompatible" in (result.stderr or result.output).lower()


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_audit_missing_required_args_exits_nonzero() -> None:
    """`byline audit` without args exits non-zero from Typer's validator."""

    result = runner.invoke(app, ["audit"])

    # Typer / Click emit exit code 2 for missing required arguments.
    assert result.exit_code != 0
    assert result.exit_code == 2


def test_scan_no_args_exits_nonzero() -> None:
    """`byline scan` without args exits non-zero (missing required arg)."""

    result = runner.invoke(app, ["scan"])

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# baseline (mocked corpus build)
# ---------------------------------------------------------------------------


def _fake_corpus(username: str) -> BaselineCorpus:
    """Tiny synthetic corpus avoiding any network access."""

    sample = WritingSample(
        source=f"README.md@{username}/example",
        kind="readme",
        text="hello world, this is a small example README",
        word_count=9,
    )
    return BaselineCorpus(
        username=username,
        samples=[sample],
        total_words=9,
        repos_scanned=[f"{username}/example"],
        repos_skipped=[],
    )


def test_baseline_json_output_with_mocked_corpus() -> None:
    """`byline baseline <user> --json` emits parseable JSON when corpus is mocked."""

    with patch("byline.cli.build_corpus", side_effect=lambda u, t: _fake_corpus(u)):
        result = runner.invoke(app, ["baseline", "alice", "--json"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["username"] == "alice"
    assert payload["repos_scanned"] == ["alice/example"]
    assert payload["total_words"] == 9
    assert "profile" in payload
    # The profile contains all eight StyleProfile fields.
    expected_fields = {
        "em_dash_density",
        "emoji_in_headers_ratio",
        "avg_sentence_length",
        "type_token_ratio",
        "typo_rate",
        "sophistication_score",
        "banner_comment_density",
        "progress_ux_score",
    }
    assert expected_fields.issubset(payload["profile"].keys())


def test_baseline_text_output_with_mocked_corpus() -> None:
    """The default baseline output is human-readable text mentioning the user."""

    with patch("byline.cli.build_corpus", side_effect=lambda u, t: _fake_corpus(u)):
        result = runner.invoke(
            app,
            ["baseline", "alice", "--no-color"],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "alice" in result.stdout
    assert "Baseline profile" in result.stdout


# ---------------------------------------------------------------------------
# audit (run_audit fully mocked so we never touch the network)
# ---------------------------------------------------------------------------


def _fake_audit_result(target: str, candidate: str | None) -> AuditResult:
    """A minimal AuditResult suitable for exercising the emit pipeline."""

    profile = StyleProfile(
        em_dash_density=0.0,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=0.0,
        type_token_ratio=0.0,
        typo_rate=0.0,
        sophistication_score=0.0,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )
    return AuditResult(
        target_repo=target,
        candidate=candidate,
        baseline=None,
        target_profile=profile,
        fingerprints=[],
        disproportions=[],
        deltas=[],
        llm_qualitative=None,
        overall_signal="aligned",
    )


def test_audit_json_and_docx_incompatible(tmp_path: Path) -> None:
    """`audit` rejects `--json` + `--docx` with exit code 3."""

    docx_file = tmp_path / "report.docx"
    result = runner.invoke(
        app,
        [
            "audit",
            str(SYNTHETIC_REPO),
            "--candidate",
            "alice",
            "--json",
            "--docx",
            str(docx_file),
        ],
    )

    assert result.exit_code == 3


def test_audit_happy_path_with_mocked_run_audit() -> None:
    """`byline audit` invokes run_audit, prints the rendered report, exits 0."""

    fake = _fake_audit_result(str(SYNTHETIC_REPO), "alice")
    with patch("byline.cli.run_audit", return_value=fake):
        result = runner.invoke(
            app,
            [
                "audit",
                str(SYNTHETIC_REPO),
                "--candidate",
                "alice",
                "--no-color",
            ],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "Comparative Attribution Report" in result.stdout


def test_audit_error_path_returns_exit_code_one() -> None:
    """A generic exception from run_audit surfaces as exit code 1."""

    with patch("byline.cli.run_audit", side_effect=RuntimeError("boom")):
        result = runner.invoke(
            app,
            ["audit", str(SYNTHETIC_REPO), "--candidate", "alice"],
        )

    assert result.exit_code == 1
    assert "boom" in (result.stderr or result.output)


# ---------------------------------------------------------------------------
# v0.2 extensions: questions, chat, align, --no-history
# ---------------------------------------------------------------------------

ALIGNED_DOCS_REPO = FIXTURES_DIR / "aligned_docs_repo"
MISALIGNED_DOCS_REPO = FIXTURES_DIR / "misaligned_docs_repo"


def test_questions_help_lists_flags() -> None:
    """`byline questions --help` mentions --candidate, -n, --json."""

    result = runner.invoke(app, ["questions", "--help"])

    assert result.exit_code == 0, result.output
    plain = _plain(result.stdout)
    assert "--candidate" in plain
    assert "-n" in plain
    assert "--json" in plain


def test_chat_help_exits_zero() -> None:
    """`byline chat --help` succeeds."""

    result = runner.invoke(app, ["chat", "--help"])

    assert result.exit_code == 0, result.output


def test_align_help_mentions_with_llm() -> None:
    """`byline align --help` mentions --with-llm."""

    result = runner.invoke(app, ["align", "--help"])

    assert result.exit_code == 0, result.output
    assert "--with-llm" in result.stdout


def test_align_aligned_fixture_emits_markdown_overall() -> None:
    """`byline align <aligned-fixture>` produces a markdown report mentioning Overall."""

    result = runner.invoke(app, ["align", str(ALIGNED_DOCS_REPO)])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "Overall" in result.stdout
    # Aligned fixture should classify as aligned (or at worst minor_gaps).
    assert "aligned" in result.stdout.lower()


def test_align_misaligned_fixture_json_is_valid() -> None:
    """`byline align <misaligned-fixture> --json` emits parseable JSON."""

    result = runner.invoke(app, ["align", str(MISALIGNED_DOCS_REPO), "--json"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert "checks" in payload
    assert "overall_alignment" in payload
    assert "deterministic_only" in payload


def test_align_nonexistent_path_exits_one() -> None:
    """`byline align /nonexistent/path` exits 1."""

    result = runner.invoke(app, ["align", "/nonexistent/path/does/not/exist"])

    assert result.exit_code == 1


def test_align_json_and_docx_incompatible(tmp_path: Path) -> None:
    """`byline align --json --docx <path>` exits 3."""

    docx_file = tmp_path / "report.docx"
    result = runner.invoke(
        app,
        [
            "align",
            str(ALIGNED_DOCS_REPO),
            "--json",
            "--docx",
            str(docx_file),
        ],
    )

    assert result.exit_code == 3


def test_questions_without_llm_key_exits_two(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`byline questions` without any provider key exits 2 with a multi-provider hint."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    # Ensure get_llm_provider returns None regardless of installed extras.
    with patch("byline.llm_provider.get_llm_provider", return_value=None):
        result = runner.invoke(app, ["questions", str(SYNTHETIC_REPO)])

    assert result.exit_code == 2
    combined = (result.stdout + (result.stderr or "")).lower()
    # The hint should mention all three configurable paths.
    assert "anthropic_api_key" in combined
    assert "openai_api_key" in combined
    assert "byline_llm_provider" in combined


def test_chat_without_llm_key_exits_two(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`byline chat` without any provider key exits 2."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BYLINE_LLM_PROVIDER", raising=False)
    with patch("byline.llm_provider.get_llm_provider", return_value=None):
        result = runner.invoke(app, ["chat", str(SYNTHETIC_REPO)])

    assert result.exit_code == 2
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "anthropic_api_key" in combined
    assert "openai_api_key" in combined
    assert "byline_llm_provider" in combined


def test_audit_help_mentions_no_history() -> None:
    """`byline audit --help` documents the new --no-history flag."""

    result = runner.invoke(app, ["audit", "--help"])

    assert result.exit_code == 0, result.output
    assert "--no-history" in _plain(result.stdout)
