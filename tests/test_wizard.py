"""Tests for the interactive ``byline wizard`` subcommand.

Exercises the prompted walkthrough via :class:`typer.testing.CliRunner`'s
``input=`` parameter. The underlying :func:`byline.compare.audit` call is
patched out so these tests never touch the network. The framing-language
assertion is load-bearing: the wizard's help must stay comparative, never
slip into "AI detector" phrasing.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from byline.cli import app
from tests.test_cli import _fake_audit_result

runner = CliRunner()


def test_wizard_help_uses_comparative_framing() -> None:
    """`byline wizard --help` describes the command comparatively."""

    result = runner.invoke(app, ["wizard", "--help"])

    assert result.exit_code == 0
    lower = result.stdout.lower()
    assert "comparative" in lower
    assert "ai detector" not in lower
    assert "detect ai" not in lower


def test_wizard_audit_path_invokes_audit_with_collected_inputs() -> None:
    """Walking through the audit prompts triggers run_audit with the collected values."""

    fake = _fake_audit_result("/some/target", "alice")
    with (
        patch("byline.wizard.audit", return_value=fake) as mock_audit,
        patch("byline.wizard.resolve_github_token", return_value="ghp_test"),
    ):
        # mode, target, candidate, with_llm (n), md path (blank), docx path (blank)
        result = runner.invoke(
            app,
            ["wizard", "--no-color"],
            input="audit\n/some/target\nalice\nn\n\n\n",
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    mock_audit.assert_called_once()
    args, kwargs = mock_audit.call_args
    # call shape: audit(target, candidate, token, with_llm=...)
    assert args[0] == "/some/target"
    assert args[1] == "alice"
    assert kwargs.get("with_llm") is False


def test_wizard_scan_path_skips_candidate_prompt() -> None:
    """Choosing 'scan' uses candidate=None and skips the LLM prompt."""

    fake = _fake_audit_result("/some/target", None)
    with (
        patch("byline.wizard.audit", return_value=fake) as mock_audit,
        patch("byline.wizard.resolve_github_token", return_value=None),
    ):
        # mode=scan, target, md blank, docx blank
        result = runner.invoke(
            app,
            ["wizard", "--no-color"],
            input="scan\n/some/target\n\n\n",
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    args, kwargs = mock_audit.call_args
    assert args[0] == "/some/target"
    assert args[1] is None
    assert kwargs.get("with_llm") is False


def test_wizard_returns_one_on_audit_failure() -> None:
    """A failure in the underlying audit call surfaces as exit code 1."""

    with (
        patch("byline.wizard.audit", side_effect=RuntimeError("boom")),
        patch("byline.wizard.resolve_github_token", return_value=None),
    ):
        result = runner.invoke(
            app,
            ["wizard", "--no-color"],
            input="audit\n/some/target\nalice\nn\n\n\n",
        )

    assert result.exit_code == 1
    assert "boom" in (result.stderr or result.output)
