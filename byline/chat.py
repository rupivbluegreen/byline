"""Interactive REPL over an ``AuditResult`` (v0.2 §5.8).

The chat session requires a working Anthropic client; without one,
``run_chat_session`` raises ``LLMUnavailableError`` immediately.

The REPL provides slash commands for inspecting the audit (``/summary``,
``/findings``), reading repo files (``/show <path>``), generating
interview questions (``/questions``), saving transcripts (``/save``), and
resetting conversation state (``/reset``). Any non-slash input is forwarded
to Claude via ``run_chat_turn``.

The assistant maintains comparative-attribution framing: if asked whether
the candidate used AI, ``CHAT_SYSTEM_PROMPT`` instructs Claude to respond
that the audit surfaces signals only. Output is additionally scrubbed by
``strip_banned_phrases`` in ``run_chat_turn``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from byline.llm import (
    CHAT_SYSTEM_PROMPT,
    LLMResponseError,
    LLMUnavailableError,
    run_chat_turn,
)
from byline.models import AuditResult

logger = logging.getLogger(__name__)


CHAT_HELP_TEXT = """REPL commands:
  /quit, /exit, /q     End session
  /help                Show commands
  /summary             Re-print audit summary
  /show <file>         Print a file from the repo (cap at 400 lines)
  /findings            List all fingerprint hits and deltas
  /questions           Generate interview questions inline
  /save <path>         Save conversation transcript
  /reset               Clear conversation history (keep audit context)
"""

MAX_HISTORY_TURNS = 30  # cap of stored exchanges
DEFAULT_TRANSCRIPT_PREFIX = "byline-chat-"

# Display cap for /show
_SHOW_LINE_CAP = 400


# ---------------------------------------------------------------------------
# Pure helpers (testable without the REPL loop)
# ---------------------------------------------------------------------------


def parse_command(line: str) -> tuple[str | None, str]:
    """Parse a REPL line into ``(command, rest)``.

    A leading ``/`` indicates a slash command; otherwise ``command`` is
    ``None`` and ``rest`` is the (stripped) input. Command names are
    lowercased; ``rest`` preserves its original case. Empty input yields
    ``(None, "")``.
    """
    line = line.strip()
    if not line.startswith("/"):
        return (None, line)
    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    return (cmd, rest)


def render_audit_summary(audit: AuditResult) -> str:
    """Return a 4-line plain-text summary of an ``AuditResult``.

    Used as the seed user message at session start and as the response to
    ``/summary``.
    """
    candidate = audit.candidate or "(not provided)"
    return (
        f"Audit summary\n"
        f"  Target: {audit.target_repo}\n"
        f"  Candidate: {candidate}\n"
        f"  Overall signal: {audit.overall_signal}\n"
        f"  Findings: {len(audit.fingerprints)} fingerprint hits, "
        f"{len(audit.disproportions)} disproportions, {len(audit.deltas)} deltas"
    )


def render_findings(audit: AuditResult) -> str:
    """Compact bullet list of fingerprint hits and comparative deltas.

    Caps total output at ~30 lines for readability. Used by ``/findings``.
    """
    lines: list[str] = []
    max_lines = 30

    lines.append(f"Fingerprint hits ({len(audit.fingerprints)}):")
    if audit.fingerprints:
        for fp in audit.fingerprints:
            if len(lines) >= max_lines - 1:
                remaining = len(audit.fingerprints) - (len(lines) - 1)
                if remaining > 0:
                    lines.append(f"  ... and {remaining} more")
                break
            loc = f"{fp.file_path}:{fp.line_number}" if fp.line_number is not None else fp.file_path
            lines.append(f"  - [{fp.category}] {fp.pattern} @ {loc}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Comparative deltas ({len(audit.deltas)}):")
    if audit.deltas:
        for delta in audit.deltas:
            if len(lines) >= max_lines:
                remaining = len(audit.deltas) - (
                    len(lines) - (lines.index("") if "" in lines else 0) - 2
                )
                if remaining > 0:
                    lines.append(f"  ... and {remaining} more")
                break
            lines.append(
                f"  - {delta.metric}: baseline={delta.baseline_value:.3f}, "
                f"target={delta.target_value:.3f} [{delta.severity}]"
            )
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers used by the REPL loop only
# ---------------------------------------------------------------------------


def _format_history_as_markdown(conversation_history: list[dict]) -> str:
    """Render the conversation history as a Markdown transcript."""
    sections: list[str] = []
    for msg in conversation_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        heading = "## User" if role == "user" else "## Assistant"
        sections.append(f"{heading}\n{content}\n")
    return "\n---\n\n".join(sections)


def _resolve_show_path(repo_path: Path, rel: str) -> Path | None:
    """Resolve ``rel`` against ``repo_path``, refusing path traversal.

    Returns the resolved path if it stays under ``repo_path``, else ``None``.
    """
    if not rel:
        return None
    # Refuse any explicit parent-directory escape in the input.
    if ".." in Path(rel).parts:
        return None
    candidate = (repo_path / rel).resolve()
    repo_resolved = repo_path.resolve()
    try:
        candidate.relative_to(repo_resolved)
    except ValueError:
        return None
    return candidate


def _prune_history(history: list[dict]) -> list[dict]:
    """Cap the in-memory conversation to the last ``MAX_HISTORY_TURNS * 2`` messages."""
    limit = MAX_HISTORY_TURNS * 2
    if len(history) <= limit:
        return history
    return history[-limit:]


def _print_assistant_reply(reply: str) -> None:
    """Render Claude's reply as Markdown via Rich.

    Imported lazily so test collection of pure helpers doesn't pay the
    Rich import cost.
    """
    from rich.console import Console
    from rich.markdown import Markdown

    Console().print(Markdown(reply))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_chat_session(
    audit: AuditResult,
    repo_path: Path,
    provider: Any = None,
    *,
    anthropic_client: Any = None,
) -> None:
    """Run an interactive REPL session over ``audit``.

    Blocks until the user exits (``/quit``, EOF, or two consecutive Ctrl+C).
    Requires a working LLM ``provider``; raises ``LLMUnavailableError``
    immediately when ``provider is None``. ``anthropic_client`` is accepted
    as a deprecated alias for ``provider``.
    """
    # Resolve deprecated alias.
    if provider is None and anthropic_client is not None:
        provider = anthropic_client

    if provider is None:
        raise LLMUnavailableError(
            "provider is None; install byline-audit[llm] and set "
            "ANTHROPIC_API_KEY (or OPENAI_API_KEY with BYLINE_LLM_PROVIDER=openai)"
        )

    # Lazy imports — keep module import cheap and test-friendly.
    from prompt_toolkit import PromptSession

    summary = render_audit_summary(audit)
    print(summary)
    print()
    print(CHAT_HELP_TEXT)

    conversation_history: list[dict] = []

    # Seed turn: prime the model with the audit summary + full JSON context.
    seed_message = f"{summary}\n\nAudit JSON: {audit.model_dump_json(indent=2)}"
    _seed_context(conversation_history, seed_message, provider)

    session: PromptSession = PromptSession()
    interrupted_once = False

    while True:
        try:
            user_input = session.prompt("byline> ")
        except KeyboardInterrupt:
            if interrupted_once:
                print()
                break
            print()
            interrupted_once = True
            continue
        except EOFError:
            print()
            break

        interrupted_once = False
        if not user_input.strip():
            continue

        cmd, rest = parse_command(user_input)

        if cmd in {"quit", "exit", "q"}:
            break
        if cmd == "help":
            print(CHAT_HELP_TEXT)
            continue
        if cmd == "summary":
            print(render_audit_summary(audit))
            continue
        if cmd == "findings":
            print(render_findings(audit))
            continue
        if cmd == "show":
            _handle_show(repo_path, rest)
            continue
        if cmd == "questions":
            _handle_questions(audit, repo_path, provider)
            continue
        if cmd == "save":
            _handle_save(conversation_history, rest)
            continue
        if cmd == "reset":
            conversation_history.clear()
            _seed_context(conversation_history, seed_message, provider)
            print("(conversation history cleared; audit context re-seeded)")
            continue
        if cmd is not None:
            print(f"Unknown command: /{cmd}. Type /help for the command list.")
            continue

        # Plain prose → LLM turn
        try:
            reply = run_chat_turn(
                CHAT_SYSTEM_PROMPT,
                conversation_history,
                user_input,
                provider=provider,
            )
        except LLMResponseError as exc:
            print(f"(LLM error: {exc})")
            continue

        conversation_history.append({"role": "user", "content": user_input})
        conversation_history.append({"role": "assistant", "content": reply})
        conversation_history[:] = _prune_history(conversation_history)
        _print_assistant_reply(reply)


# ---------------------------------------------------------------------------
# Command handlers (split out for readability)
# ---------------------------------------------------------------------------


def _seed_context(
    conversation_history: list[dict],
    seed_message: str,
    provider: Any,
) -> None:
    """Send the initial audit-context turn and record both sides."""
    try:
        reply = run_chat_turn(
            CHAT_SYSTEM_PROMPT,
            conversation_history,
            seed_message,
            provider=provider,
        )
    except LLMResponseError as exc:
        print(f"(LLM error while seeding audit context: {exc})")
        return

    conversation_history.append({"role": "user", "content": seed_message})
    conversation_history.append({"role": "assistant", "content": reply})


def _handle_show(repo_path: Path, rest: str) -> None:
    """Print up to ``_SHOW_LINE_CAP`` lines of a repo-relative file."""
    target = rest.strip()
    if not target:
        print("Usage: /show <file>")
        return
    resolved = _resolve_show_path(repo_path, target)
    if resolved is None:
        print(f"Refusing to read: {target} (path traversal or outside repo)")
        return
    if not resolved.exists():
        print(f"Not found: {target}")
        return
    if not resolved.is_file():
        print(f"Not a regular file: {target}")
        return
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Could not read {target}: {exc}")
        return
    lines = text.splitlines()
    display_count = min(len(lines), _SHOW_LINE_CAP)
    width = len(str(display_count))
    print(f"--- {target} ({display_count}/{len(lines)} lines) ---")
    for idx in range(display_count):
        print(f"{idx + 1:>{width}}  {lines[idx]}")
    if len(lines) > _SHOW_LINE_CAP:
        print(f"... ({len(lines) - _SHOW_LINE_CAP} more lines omitted)")


def _handle_questions(audit: AuditResult, repo_path: Path, provider: Any) -> None:
    """Invoke ``byline.questions.generate_questions`` and print the results."""
    try:
        # Deferred import: questions.py may not exist yet during parallel
        # development and we don't want the chat module to fail import.
        from byline.questions import generate_questions
    except ImportError as exc:
        print(f"(Questions module unavailable: {exc})")
        return

    try:
        question_set = generate_questions(
            audit=audit,
            repo_path=repo_path,
            provider=provider,
            n=5,
        )
    except LLMUnavailableError as exc:
        print(f"(LLM required for /questions: {exc})")
        return
    except LLMResponseError as exc:
        print(f"(LLM error: {exc})")
        return
    except Exception as exc:  # noqa: BLE001 — defensive; do not kill the REPL
        print(f"(Question generation failed: {exc})")
        return

    questions = getattr(question_set, "questions", None)
    if not questions:
        print("(no questions generated)")
        return
    for idx, q in enumerate(questions, start=1):
        text = getattr(q, "text", str(q))
        grounding_file = getattr(q, "grounding_file", None)
        grounding_line = getattr(q, "grounding_line", None)
        loc = ""
        if grounding_file:
            loc = (
                f" [{grounding_file}:{grounding_line}]"
                if grounding_line is not None
                else f" [{grounding_file}]"
            )
        print(f"{idx}. {text}{loc}")


def _handle_save(conversation_history: list[dict], rest: str) -> None:
    """Write the conversation history to a Markdown transcript."""
    target = rest.strip()
    if not target:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = f"{DEFAULT_TRANSCRIPT_PREFIX}{stamp}.md"
    path = Path(target)
    body = _format_history_as_markdown(conversation_history)
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        print(f"Could not save to {target}: {exc}")
        return
    print(f"Saved transcript to {path}")
