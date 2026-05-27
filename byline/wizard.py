"""Interactive guided walkthrough of the comparative audit pipeline.

A friendly alternative to remembering the ``audit`` / ``scan`` flag set:
prompts for the mode, submission target, candidate username, optional LLM
pass, and output paths, then dispatches to :func:`byline.compare.audit`.
Every prompt and status line stays inside the project's comparative framing —
signals and divergence, never authorship verdicts.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

from byline.cli import _emit
from byline.compare import audit
from byline.config import resolve_github_token


def run_wizard(no_color: bool) -> int:
    """Interactive guided walkthrough of an audit or scan run.

    Returns 0 on success, 1 on failure. Never raises out of this function.
    """

    console = Console(no_color=no_color, force_terminal=not no_color)

    mode = Prompt.ask("Mode", choices=["scan", "audit"], default="audit")
    target = Prompt.ask("Submission target (local path or GitHub URL)")
    if mode == "audit":
        candidate: str | None = Prompt.ask("Candidate GitHub username")
        with_llm = Confirm.ask("Run optional LLM qualitative pass?", default=False)
    else:
        candidate = None
        with_llm = False

    output_md_raw = Prompt.ask(
        "Markdown output path (blank to print to stdout)",
        default="",
    )
    output_md = Path(output_md_raw) if output_md_raw.strip() else None

    output_docx_raw = Prompt.ask(
        "Also render a .docx report? Path or blank to skip",
        default="",
    )
    output_docx = Path(output_docx_raw) if output_docx_raw.strip() else None

    token = resolve_github_token(None)
    if not token:
        logging.getLogger("byline.wizard").warning(
            "No GITHUB_TOKEN provided; subject to anonymous rate limits."
        )

    console.print(f"Running {mode} against [bold]{target}[/bold]...")

    try:
        result = audit(target, candidate, token, with_llm=with_llm)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {exc}", err=True)
        return 1

    _emit(result, output_md, output_docx, json_output=False, no_color=no_color)
    return 0
