"""Typer CLI surface exposing ``audit``, ``baseline``, and ``scan``. See spec §4.

This module is the user-facing entry point for ``byline``. All help text,
log messages, and error strings are framed as *comparative attribution* —
they talk about *signals*, *baselines*, and *divergence* between a
candidate's prior writing surface and a target repository. The CLI never
claims to classify writing as machine-authored or to render a verdict on
authorship; that framing is load-bearing for the product and is enforced by
the test suite.

Exit codes (spec §4):

* ``0`` — success
* ``1`` — general failure (network error, unreadable target, etc.)
* ``2`` — GitHub authentication missing or rate-limited (raised by
  :mod:`byline.github_client` directly via ``SystemExit(2)``)
* ``3`` — invalid CLI argument combination (e.g. ``--json`` + ``--docx``)
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

from byline import __version__
from byline.compare import (
    audit as run_audit,
)
from byline.compare import (
    compute_baseline_profile,
)
from byline.config import configure_logging, resolve_github_token
from byline.corpus import build_corpus
from byline.models import AuditResult
from byline.report import render_markdown
from byline.report_docx import render_docx

app = typer.Typer(
    name="byline",
    help="Comparative attribution analysis for take-home submissions.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)


# ---------------------------------------------------------------------------
# Root callback (handles --version)
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    """Print the package version and exit when ``--version`` is supplied."""

    if value:
        typer.echo(f"byline {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool | None = typer.Option(  # noqa: B008 — Typer pattern
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Byline — comparative attribution analysis for take-home submissions."""


# ---------------------------------------------------------------------------
# Shared output helper
# ---------------------------------------------------------------------------


def _emit(
    result: AuditResult,
    output: Path | None,
    docx: Path | None,
    json_output: bool,
    no_color: bool,
) -> None:
    """Render an :class:`AuditResult` in the format the user asked for.

    ``--json`` and ``--docx`` cover machine-readable and Word-doc deliverables
    respectively; the default emits the Markdown report rendered by
    :func:`byline.report.render_markdown`. ``--output`` redirects whichever
    text format is active (Markdown or JSON) to a file; ``--docx`` is always
    a file path.
    """

    if json_output:
        text = result.model_dump_json(indent=2)
        if output:
            output.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)
        return

    markdown = render_markdown(result)
    if output:
        output.write_text(markdown, encoding="utf-8")
    elif no_color:
        typer.echo(markdown)
    else:
        Console().print(markdown)

    if docx:
        render_docx(result, docx)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@app.command()
def audit(
    target: str = typer.Argument(
        ...,
        help="Target repository: GitHub URL or local filesystem path.",
    ),
    candidate: str = typer.Option(
        ...,
        "--candidate",
        help="Candidate's GitHub username (baseline source).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write the Markdown (or JSON) report to this path instead of stdout.",
    ),
    docx: Path | None = typer.Option(
        None,
        "--docx",
        help="Also render a Word .docx report to this path.",
    ),
    with_llm: bool = typer.Option(
        False,
        "--with-llm",
        help="Run the optional Claude qualitative pass over the assembled signals.",
    ),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub PAT for higher rate limits; falls back to $GITHUB_TOKEN.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON instead of Markdown. Incompatible with --docx.",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output.",
    ),
    no_history: bool = typer.Option(
        False,
        "--no-history",
        help="Skip commit forensics (faster, for shallow or non-git targets).",
    ),
) -> None:
    """Run the full comparative audit of TARGET against CANDIDATE's baseline.

    Builds the candidate's writing baseline from their public GitHub footprint,
    derives a parallel style profile for the target repository, and reports
    the divergence between the two as comparative signals — never as a
    verdict on authorship.
    """

    configure_logging(verbose)
    if json_output and docx:
        typer.echo("error: --json is incompatible with --docx", err=True)
        raise typer.Exit(3)

    token = resolve_github_token(github_token)
    if not token:
        logging.getLogger("byline.cli").warning(
            "No GITHUB_TOKEN provided; subject to anonymous rate limits."
        )

    try:
        import inspect

        sig = inspect.signature(run_audit)
        kwargs: dict[str, object] = {"with_llm": with_llm}
        if "with_history" in sig.parameters:
            kwargs["with_history"] = not no_history
        result = run_audit(target, candidate, token, **kwargs)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _emit(result, output, docx, json_output, no_color)


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


@app.command()
def baseline(
    username: str = typer.Argument(..., help="GitHub username to build a baseline for."),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub PAT for higher rate limits; falls back to $GITHUB_TOKEN.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the baseline profile as JSON.",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output.",
    ),
) -> None:
    """Build and print the candidate's writing-style baseline profile.

    Useful for sanity-checking the baseline before running a full audit, and
    for understanding which prior repositories contributed to the signal
    surface.
    """

    configure_logging(verbose)
    token = resolve_github_token(github_token)
    if not token:
        logging.getLogger("byline.cli").warning(
            "No GITHUB_TOKEN provided; subject to anonymous rate limits."
        )

    try:
        corpus = build_corpus(username, token)
        profile = compute_baseline_profile(corpus)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        payload = {
            "username": corpus.username,
            "repos_scanned": corpus.repos_scanned,
            "repos_skipped": [list(item) for item in corpus.repos_skipped],
            "total_words": corpus.total_words,
            "profile": profile.model_dump(),
        }
        import json as _json

        typer.echo(_json.dumps(payload, indent=2))
        return

    console = Console(no_color=no_color, force_terminal=not no_color)
    console.print(f"[bold]Baseline profile for {username}[/bold]")
    console.print(f"Repos scanned: {len(corpus.repos_scanned)}")
    console.print(f"Total words: {corpus.total_words}")
    for key, value in profile.model_dump().items():
        console.print(f"  {key}: {value:.4f}")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@app.command()
def scan(
    target: str = typer.Argument(
        ...,
        help="Target repository URL or local filesystem path.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write the Markdown (or JSON) report to this path instead of stdout.",
    ),
    docx: Path | None = typer.Option(
        None,
        "--docx",
        help="Also render a Word .docx report to this path.",
    ),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub PAT for higher rate limits; falls back to $GITHUB_TOKEN.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON instead of Markdown. Incompatible with --docx.",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output.",
    ),
) -> None:
    """Run fingerprint and disproportion analysis on TARGET without a baseline.

    Skips the candidate baseline build entirely. Use this when you want a
    quick read on the target's writing surface and structural ratios without
    pulling a candidate's GitHub history.
    """

    configure_logging(verbose)
    if json_output and docx:
        typer.echo("error: --json is incompatible with --docx", err=True)
        raise typer.Exit(3)

    token = resolve_github_token(github_token)
    try:
        result = run_audit(target, candidate_username=None, github_token=token, with_llm=False)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _emit(result, output, docx, json_output, no_color)


# ---------------------------------------------------------------------------
# questions (LLM required)
# ---------------------------------------------------------------------------


@app.command()
def questions(
    target: str = typer.Argument(
        ...,
        help="Target repository: GitHub URL or local filesystem path.",
    ),
    candidate: str | None = typer.Option(
        None,
        "--candidate",
        help="Candidate's GitHub username (optional, for richer grounding).",
    ),
    n: int = typer.Option(
        10,
        "-n",
        help="Number of grounded interview questions to generate.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write the rendered Markdown (or JSON) to this path instead of stdout.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured JSON QuestionSet instead of Markdown.",
    ),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub PAT for higher rate limits; falls back to $GITHUB_TOKEN.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
) -> None:
    """Generate grounded interview questions from a candidate's submission.

    Builds a comparative audit (deterministic-only) of TARGET, then asks
    Claude for ``n`` interview questions anchored in specific files and
    signals. Requires the ``[llm]`` extras and ``ANTHROPIC_API_KEY`` in the
    environment. Questions probe engineering decisions, never authorship.
    """

    configure_logging(verbose)
    token = resolve_github_token(github_token)

    # Build the audit first (deterministic only — questions are about the audit).
    try:
        audit_result = run_audit(target, candidate, token, with_llm=False)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    # Resolve provider — lazy import keeps the top-level module light.
    from byline.llm import LLMUnavailableError
    from byline.llm_provider import get_llm_provider

    provider = get_llm_provider()
    if provider is None:
        typer.echo(
            "error: This command requires the LLM extras and an API key for the "
            "configured provider.\n"
            "  Default: Anthropic — set ANTHROPIC_API_KEY.\n"
            "  Or use OpenAI / a self-hosted endpoint:\n"
            "     export BYLINE_LLM_PROVIDER=openai\n"
            "     export OPENAI_API_KEY=sk-...                    "
            "# or any value for self-hosted\n"
            "     export OPENAI_BASE_URL=http://localhost:11434/v1   "
            "# for Ollama (optional)\n"
            "  Install the extras with: pip install 'byline-audit[llm]'",
            err=True,
        )
        raise typer.Exit(2)

    try:
        from byline.questions import generate_questions

        # If ``target`` is a local path, hand it through verbatim. If it's a
        # URL, the audit_result's clone may already be gone — fall back to cwd
        # as a pragmatic v0.2 trade-off.
        repo_path = Path(target) if Path(target).exists() else Path(".")
        question_set = generate_questions(audit_result, repo_path, provider=provider, n=n)
    except LLMUnavailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        text = question_set.model_dump_json(indent=2)
    else:
        lines: list[str] = ["# Interview Questions", ""]
        for i, q in enumerate(question_set.questions, 1):
            lines.append(f"## Q{i}. {q.text}")
            if q.grounding_file:
                anchor = q.grounding_file + (f":{q.grounding_line}" if q.grounding_line else "")
                lines.append(f"_Grounding: {anchor}_  ")
            lines.append(f"_Rationale: {q.rationale}_  ")
            if q.signal_addressed:
                lines.append(f"_Signal: {q.signal_addressed}_  ")
            lines.append("")
        text = "\n".join(lines)

    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)


# ---------------------------------------------------------------------------
# chat (LLM required)
# ---------------------------------------------------------------------------


@app.command()
def chat(
    target: str = typer.Argument(
        ...,
        help="Target repository: GitHub URL or local filesystem path.",
    ),
    candidate: str | None = typer.Option(
        None,
        "--candidate",
        help="Candidate's GitHub username (optional baseline source).",
    ),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub PAT for higher rate limits; falls back to $GITHUB_TOKEN.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
) -> None:
    """Open an interactive REPL over the comparative audit findings.

    Builds a deterministic audit of TARGET, then drops into a chat session
    where reviewers can ask Claude follow-up questions about the signals.
    Requires the ``[llm]`` extras and ``ANTHROPIC_API_KEY``.
    """

    configure_logging(verbose)
    token = resolve_github_token(github_token)

    try:
        audit_result = run_audit(target, candidate, token, with_llm=False)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    from byline.llm import LLMUnavailableError
    from byline.llm_provider import get_llm_provider

    provider = get_llm_provider()
    if provider is None:
        typer.echo(
            "error: This command requires the LLM extras and an API key for the "
            "configured provider.\n"
            "  Default: Anthropic — set ANTHROPIC_API_KEY.\n"
            "  Or use OpenAI / a self-hosted endpoint:\n"
            "     export BYLINE_LLM_PROVIDER=openai\n"
            "     export OPENAI_API_KEY=sk-...                    "
            "# or any value for self-hosted\n"
            "     export OPENAI_BASE_URL=http://localhost:11434/v1   "
            "# for Ollama (optional)\n"
            "  Install the extras with: pip install 'byline-audit[llm]'",
            err=True,
        )
        raise typer.Exit(2)

    from byline.chat import run_chat_session

    repo_path = Path(target) if Path(target).exists() else Path(".")
    try:
        run_chat_session(audit_result, repo_path, provider=provider)
    except LLMUnavailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


# ---------------------------------------------------------------------------
# align (LLM optional)
# ---------------------------------------------------------------------------


@app.command()
def align(
    target: str = typer.Argument(
        ...,
        help="Local filesystem path to the repository to check.",
    ),
    with_llm: bool = typer.Option(
        False,
        "--with-llm",
        help="Add semantic alignment via Claude (requires the [llm] extras).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write the rendered Markdown (or JSON) to this path instead of stdout.",
    ),
    docx: Path | None = typer.Option(
        None,
        "--docx",
        help="Reserved for future docx export of alignment findings.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON instead of Markdown. Incompatible with --docx.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase logging verbosity. Repeat (-vv) for DEBUG.",
    ),
) -> None:
    """Run the documentation-implementation alignment check on TARGET.

    Surfaces deterministic gaps between the README's promises and the code's
    delivery as a comparative signal — never as a verdict on authorship. The
    optional ``--with-llm`` pass adds semantic alignment checks.
    """

    configure_logging(verbose)
    if json_output and docx:
        typer.echo("error: --json is incompatible with --docx", err=True)
        raise typer.Exit(3)

    repo_path = Path(target)
    if not repo_path.exists():
        typer.echo(f"error: target path does not exist: {target}", err=True)
        raise typer.Exit(1)

    from byline.alignment import check_alignment
    from byline.llm_provider import get_llm_provider

    provider = get_llm_provider() if with_llm else None
    if with_llm and provider is None:
        typer.echo(
            "warning: --with-llm requested but no LLM provider is configured.\n"
            "  Default: Anthropic — set ANTHROPIC_API_KEY.\n"
            "  Or use OpenAI / a self-hosted endpoint:\n"
            "     export BYLINE_LLM_PROVIDER=openai\n"
            "     export OPENAI_API_KEY=sk-...                    "
            "# or any value for self-hosted\n"
            "     export OPENAI_BASE_URL=http://localhost:11434/v1   "
            "# for Ollama (optional)\n"
            "  Install the extras with: pip install 'byline-audit[llm]'\n"
            "  Falling back to deterministic-only alignment.",
            err=True,
        )

    try:
        findings = check_alignment(repo_path, with_llm=with_llm, provider=provider)
    except SystemExit:
        raise
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        text = findings.model_dump_json(indent=2)
    else:
        mode = "deterministic-only" if findings.deterministic_only else "deterministic + semantic"
        lines: list[str] = [
            "# Documentation-Implementation Alignment",
            "",
            f"Mode: {mode}",
            f"Overall: **{findings.overall_alignment}**",
            "",
        ]
        if findings.llm_summary:
            lines.extend([findings.llm_summary, ""])
        if findings.checks:
            lines.append("## Checks")
            lines.append("")
            lines.append("| Kind | Source | Severity | Description |")
            lines.append("|---|---|---|---|")
            for c in findings.checks:
                desc = c.description.replace("|", "\\|")
                lines.append(f"| {c.kind} | {c.source} | {c.severity} | {desc} |")
        else:
            lines.append("_No alignment issues found._")
        text = "\n".join(lines)

    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)

    if docx:
        # v0.2: docx export from align is intentionally deferred — the
        # AlignmentFindings shape doesn't map cleanly onto the AuditResult
        # report renderer yet. Emit a clear note rather than crashing.
        typer.echo(
            "note: --docx for align is not yet implemented; markdown output produced.",
            err=True,
        )
