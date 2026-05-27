"""Optional Anthropic-backed qualitative commentary pass. See spec §10.

The LLM is asked to *interpret* comparative signals, never to determine
authorship. All output language must use words like "divergence", "signals",
"indicators", and "comparative analysis". The module is a no-op (returns
``None``) when ``ANTHROPIC_API_KEY`` is unset or the ``anthropic`` package
is not installed — failure to call the LLM never crashes an audit.

v0.2 §7 adds prompt constants and helper functions for the alignment,
questions, and chat features. These additions sit alongside v0.1's
``qualitative_pass`` (unchanged) and use a defense-in-depth post-processor
(``strip_banned_phrases``) that scrubs prohibited terminology from any LLM
output before it reaches the caller.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from importlib import import_module
from typing import Any

from byline.models import AuditResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions (v0.2 §7)
# ---------------------------------------------------------------------------


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM is required but ANTHROPIC_API_KEY is missing
    or the ``anthropic`` package isn't installed."""


class LLMResponseError(RuntimeError):
    """Raised when the LLM returns malformed output (e.g., invalid JSON)."""


# ---------------------------------------------------------------------------
# System prompt — non-negotiable framing for the LLM.
# ---------------------------------------------------------------------------
#
# This text *intentionally* mentions banned phrases ("AI detector",
# "AI-written", etc.) — but only as prohibitions directed at the LLM. The
# module itself emits none of these phrases in its own output.

SYSTEM_PROMPT = """You are an interpretation assistant for `byline`, a comparative attribution \
analysis tool. Given a structured summary of stylistic signals comparing a \
candidate's submission to their own observable writing baseline, produce a \
short qualitative paragraph (about 200 words) that helps a reviewer make \
sense of the signals.

STRICT RULES:
- Never claim that the submission was written by AI, by a different author, \
or by anyone other than the candidate. These outputs are SIGNALS, not \
VERDICTS.
- Never use the phrases "AI detector", "detect AI", "AI-written", or assert \
that "the candidate used AI".
- Use words like: divergence, signals, indicators, comparative, alignment.
- Acknowledge limitations: false positives are possible (non-native English \
writers, proofread submissions, tutorial-derived code, team-authored repos).
- Plain prose, no bullet lists, no markdown headers.
- About 200 words.

Your output is embedded in a user-visible report under a "Qualitative \
interpretation" section. Write for a hiring reviewer who is reading the \
signals alongside your prose."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def qualitative_pass(result: AuditResult) -> str | None:
    """Optional Claude qualitative interpretation of comparative signals.

    Returns the model's prose output, or ``None`` if the LLM pass is skipped
    or fails for any reason. Never raises.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set; skipping LLM qualitative pass")
        return None

    try:
        anthropic = import_module("anthropic")
    except ImportError:
        logger.warning("anthropic package not installed; install byline[llm] to enable")
        return None

    user_message = _build_user_message(result)

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            temperature=0.2,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic API error during qualitative pass: %s", exc)
        return None
    except Exception as exc:  # network errors, auth errors, etc.
        logger.warning("Unexpected error during qualitative pass: %s", exc)
        return None

    return "".join(block.text for block in message.content if hasattr(block, "text"))


# ---------------------------------------------------------------------------
# Structured-summary builder — what we hand to the LLM.
# ---------------------------------------------------------------------------


def _build_user_message(result: AuditResult) -> str:
    """Render a compact structured summary of an ``AuditResult``.

    Only aggregated / numeric signals are included. Raw submission text is
    NEVER passed through this function.
    """
    candidate = result.candidate if result.candidate else "not provided"
    lines: list[str] = [
        f"Target: {result.target_repo}",
        f"Candidate: {candidate}",
        f"Overall signal: {result.overall_signal}",
        "",
        "Comparative deltas:",
    ]

    if result.deltas:
        for delta in result.deltas:
            lines.append(
                f"- {delta.metric}: baseline={delta.baseline_value:.3f}, "
                f"target={delta.target_value:.3f}, severity={delta.severity}"
            )
    else:
        lines.append("- (no deltas available)")

    lines.append("")
    lines.append(f"Fingerprint hits (count={len(result.fingerprints)}):")
    if result.fingerprints:
        category_counts = Counter(fp.category for fp in result.fingerprints)
        pattern_counts = Counter(fp.pattern for fp in result.fingerprints)
        category_repr = ", ".join(
            f"{cat}: {count}" for cat, count in sorted(category_counts.items())
        )
        lines.append(f"- by category: {{{category_repr}}}")
        top_patterns = pattern_counts.most_common(5)
        top_repr = ", ".join(f"{pattern}: {count}" for pattern, count in top_patterns)
        lines.append(f"- top 5 patterns by frequency: {{{top_repr}}}")
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("Disproportion findings:")
    if result.disproportions:
        for finding in result.disproportions:
            lines.append(
                f"- {finding.name}: observed={finding.observed:.3f}, severity={finding.severity}"
            )
    else:
        lines.append("- (none)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# v0.2 §7 — System prompts for alignment / questions / chat
# ---------------------------------------------------------------------------
#
# These prompts intentionally name the banned phrases ("AI detector",
# "detect AI", "the candidate used AI") only as *prohibitions directed at the
# LLM*. Outputs are additionally post-processed by ``strip_banned_phrases``
# below — defense in depth.

ALIGNMENT_SEMANTIC_SYSTEM_PROMPT = """You are reviewing a code repository submitted as a take-home hiring assignment. The user has already run deterministic checks for missing CLI flags, env vars, commands, and dependencies. Your job is to identify the deeper SEMANTIC mismatches between what the README claims and what the code actually implements — features described but not implemented, behaviors not documented, config values documented but never read.

Output ONLY a JSON array. Each item has:
  - kind: doc_claims_feature_not_in_code |
          code_behavior_not_documented |
          config_documented_but_unused |
          command_documented_but_missing
  - description: one sentence, factual, no speculation
  - doc_location: file:line if known else null
  - code_location: file:line if known else null
  - severity: info | notable | significant

Do NOT comment on authorship, AI use, or candidate behavior. If unsure, omit. Return [] if aligned."""

ALIGNMENT_SUMMARY_PROMPT = """Given the alignment checks above (deterministic and semantic), write a 3-4 sentence plain-prose summary for the hiring reviewer. Frame as comparative signals. Do not pass judgment on authorship. Do not use the phrases "AI detector", "detect AI", or "the candidate used AI"."""

QUESTIONS_SYSTEM_PROMPT = """You are helping a hiring manager design follow-up interview questions for a candidate based on their take-home submission. Each question must be:
  - grounded in a specific file and line range
  - designed to test the candidate's engineering understanding, not their writing style or AI use
  - open-ended (no yes/no questions)
  - answerable in 2-3 minutes of conversation

Do NOT ask whether the candidate used AI. Do NOT frame any question around authorship. The audit findings are context for YOU; questions must probe engineering decisions, tradeoffs, and operational understanding.

Return a JSON array of question objects:
  - text
  - grounding_file
  - grounding_line (or null)
  - signal_addressed (internal label)
  - rationale (one sentence)"""

CHAT_SYSTEM_PROMPT = """You are an analyst assisting a hiring manager review a candidate's take-home submission. You have full audit findings and access to repo files.

Answer questions directly and concisely. Reference specific files and line numbers when relevant. On subjective questions, give your assessment rather than hedging.

Maintain the comparative-attribution framing: discuss signals of stylistic divergence, but do not assert that the candidate used AI or pass judgment on authorship. If asked directly whether the candidate used AI, respond that the audit surfaces signals only and the determination is the user's to make."""


# ---------------------------------------------------------------------------
# Banned-phrase post-processor (defense in depth)
# ---------------------------------------------------------------------------


BANNED_PHRASES: list[str] = [
    "AI detector",
    "detect AI",
    "ai detector",
    "the candidate used AI",
]


def strip_banned_phrases(text: str) -> str:
    """Remove or rephrase banned phrases from LLM output before returning to caller.

    Replacement strategy: replace each banned phrase with ``[redacted]`` so
    the user knows it was stripped. Case-insensitive matching.
    """
    result = text
    for phrase in BANNED_PHRASES:
        result = re.sub(re.escape(phrase), "[redacted]", result, flags=re.IGNORECASE)
    return result


# ---------------------------------------------------------------------------
# Anthropic client helper (lazy import, env-driven)
# ---------------------------------------------------------------------------


def get_anthropic_client() -> Any:
    """Try to instantiate an Anthropic client.

    Returns ``None`` if the package isn't installed or ``ANTHROPIC_API_KEY``
    is missing. Centralizes the lazy-import / no-key fallback used by other
    modules (questions.py, chat.py).
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        anthropic = import_module("anthropic")
    except ImportError:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Anthropic call wrapper used by the three v0.2 entry points
# ---------------------------------------------------------------------------


def _call_anthropic(
    client: Any,
    system_prompt: str,
    user_content: str,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> str:
    """Single-turn Anthropic call. Raises ``LLMUnavailableError`` on missing
    client and ``LLMResponseError`` on API failures."""

    if client is None:
        raise LLMUnavailableError(
            "anthropic_client is None; install byline[llm] and set ANTHROPIC_API_KEY"
        )
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        logger.debug(
            "anthropic call: input=%s output=%s",
            getattr(getattr(response, "usage", None), "input_tokens", None),
            getattr(getattr(response, "usage", None), "output_tokens", None),
        )
        return text
    except (LLMUnavailableError, LLMResponseError):
        raise
    except Exception as exc:
        raise LLMResponseError(f"Anthropic call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# v0.2 §7 — Public entry points
# ---------------------------------------------------------------------------


def run_alignment_semantic(
    audit_summary: dict[str, Any],
    readme_text: str,
    sampled_code: dict[str, str],
    anthropic_client: Any,
) -> tuple[list[dict], str]:
    """Call Claude with ``ALIGNMENT_SEMANTIC_SYSTEM_PROMPT``.

    Returns ``(list_of_check_dicts, llm_summary_text)``.
    ``list_of_check_dicts`` is parsed JSON. ``llm_summary_text`` comes from a
    follow-up call using ``ALIGNMENT_SUMMARY_PROMPT``. Both outputs are passed
    through ``strip_banned_phrases``.
    """
    if anthropic_client is None:
        raise LLMUnavailableError(
            "anthropic_client is None; install byline[llm] and set ANTHROPIC_API_KEY"
        )

    code_excerpt_lines: list[str] = []
    for path, snippet in sampled_code.items():
        code_excerpt_lines.append(f"--- {path} ---")
        code_excerpt_lines.append(snippet)

    semantic_user_content = (
        "Audit summary (JSON):\n"
        f"{json.dumps(audit_summary, default=str, indent=2)}\n\n"
        "README:\n"
        f"{readme_text}\n\n"
        "Sampled code:\n" + ("\n".join(code_excerpt_lines) if code_excerpt_lines else "(none)")
    )

    raw_checks = _call_anthropic(
        anthropic_client,
        ALIGNMENT_SEMANTIC_SYSTEM_PROMPT,
        semantic_user_content,
        max_tokens=1500,
        temperature=0.2,
    )
    cleaned_checks = strip_banned_phrases(raw_checks)

    try:
        checks = json.loads(cleaned_checks)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"Alignment semantic pass returned non-JSON output: {exc}") from exc
    if not isinstance(checks, list):
        raise LLMResponseError("Alignment semantic pass JSON was not a list")

    summary_user_content = (
        "Audit summary (JSON):\n"
        f"{json.dumps(audit_summary, default=str, indent=2)}\n\n"
        "Semantic alignment checks (JSON):\n"
        f"{json.dumps(checks, indent=2)}"
    )
    raw_summary = _call_anthropic(
        anthropic_client,
        ALIGNMENT_SUMMARY_PROMPT,
        summary_user_content,
        max_tokens=1500,
        temperature=0.2,
    )
    summary = strip_banned_phrases(raw_summary)

    return checks, summary


def run_questions(
    audit_summary: dict[str, Any],
    sampled_excerpts: dict[str, str],
    n: int,
    anthropic_client: Any,
) -> list[dict]:
    """Call Claude with ``QUESTIONS_SYSTEM_PROMPT``, asking for ``n`` questions.

    Returns parsed JSON list of question dicts. Each question's ``text`` is
    passed through ``strip_banned_phrases``.
    """
    if anthropic_client is None:
        raise LLMUnavailableError(
            "anthropic_client is None; install byline[llm] and set ANTHROPIC_API_KEY"
        )

    excerpt_lines: list[str] = []
    for path, snippet in sampled_excerpts.items():
        excerpt_lines.append(f"--- {path} ---")
        excerpt_lines.append(snippet)

    user_content = (
        f"Generate exactly {n} follow-up interview questions.\n\n"
        "Audit summary (JSON):\n"
        f"{json.dumps(audit_summary, default=str, indent=2)}\n\n"
        "Sampled code excerpts:\n" + ("\n".join(excerpt_lines) if excerpt_lines else "(none)")
    )

    raw = _call_anthropic(
        anthropic_client,
        QUESTIONS_SYSTEM_PROMPT,
        user_content,
        max_tokens=2000,
        temperature=0.2,
    )

    try:
        questions = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"Questions pass returned non-JSON output: {exc}") from exc
    if not isinstance(questions, list):
        raise LLMResponseError("Questions pass JSON was not a list")

    cleaned: list[dict] = []
    for q in questions:
        if not isinstance(q, dict):
            raise LLMResponseError("Question entry was not an object")
        cleaned_q = dict(q)
        if "text" in cleaned_q and isinstance(cleaned_q["text"], str):
            cleaned_q["text"] = strip_banned_phrases(cleaned_q["text"])
        if "rationale" in cleaned_q and isinstance(cleaned_q["rationale"], str):
            cleaned_q["rationale"] = strip_banned_phrases(cleaned_q["rationale"])
        cleaned.append(cleaned_q)
    return cleaned


def run_chat_turn(
    system_prompt: str,
    conversation_history: list[dict],
    user_message: str,
    anthropic_client: Any,
) -> str:
    """One chat turn.

    Appends ``user_message`` to ``conversation_history``, calls Claude, and
    returns the text reply (post-processed via ``strip_banned_phrases``).
    The caller is responsible for persisting the updated history; this
    function does not mutate the passed-in list.
    """
    if anthropic_client is None:
        raise LLMUnavailableError(
            "anthropic_client is None; install byline[llm] and set ANTHROPIC_API_KEY"
        )

    messages = list(conversation_history) + [{"role": "user", "content": user_message}]
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            temperature=0.2,
            system=system_prompt,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        logger.debug(
            "anthropic chat call: input=%s output=%s",
            getattr(getattr(response, "usage", None), "input_tokens", None),
            getattr(getattr(response, "usage", None), "output_tokens", None),
        )
    except Exception as exc:
        raise LLMResponseError(f"Anthropic chat call failed: {exc}") from exc

    return strip_banned_phrases(text)
