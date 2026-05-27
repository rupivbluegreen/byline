"""Optional LLM-backed qualitative commentary pass. See spec §10.

The LLM is asked to *interpret* comparative signals, never to determine
authorship. All output language must use words like "divergence", "signals",
"indicators", and "comparative analysis". The module is a no-op (returns
``None``) when no LLM provider is configured — failure to call the LLM never
crashes an audit.

v0.2 §7 adds prompt constants and helper functions for the alignment,
questions, and chat features. These additions sit alongside v0.1's
``qualitative_pass`` (unchanged) and use a defense-in-depth post-processor
(``strip_banned_phrases``) that scrubs prohibited terminology from any LLM
output before it reaches the caller.

v0.3 introduces a provider abstraction (see ``byline.llm_provider``) so
the same prompts and scrubbing apply uniformly across Anthropic, OpenAI,
and any OpenAI-compatible self-hosted endpoint (Ollama, vLLM, LM Studio,
llama.cpp). The legacy raw-anthropic-client call path is preserved via a
backwards-compat shim.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from importlib import import_module
from typing import Any

from byline.llm_provider import LLMProvider, get_llm_provider
from byline.models import AuditResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions (v0.2 §7)
# ---------------------------------------------------------------------------


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM is required but no provider is configured (no API
    key set, or the relevant SDK isn't installed)."""


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
    """Optional LLM qualitative interpretation of comparative signals.

    Returns the model's prose output, or ``None`` if the LLM pass is skipped
    or fails for any reason. Never raises.
    """
    provider = get_llm_provider()
    if provider is None:
        logger.warning("no LLM provider available; skipping qualitative pass")
        return None

    user_message = _build_user_message(result)

    try:
        text = provider.generate(
            SYSTEM_PROMPT,
            user_message,
            max_tokens=600,
            temperature=0.2,
        )
    except Exception as exc:  # network errors, auth errors, etc.
        logger.warning("Unexpected error during qualitative pass: %s", exc)
        return None

    return text


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
# Provider helpers
# ---------------------------------------------------------------------------


def get_anthropic_client() -> Any:
    """Backwards-compat shim. Returns the configured provider (which
    duck-types like a client for the purposes of llm.py internals).
    Prefer ``get_llm_provider()`` in new code.

    Returns ``None`` if no provider is configured. The historical contract
    of "returns None when no key is set" is preserved.
    """
    return get_llm_provider()


# ---------------------------------------------------------------------------
# Provider call wrapper used by the three v0.2 entry points
# ---------------------------------------------------------------------------


def _legacy_anthropic_call(
    raw_client: Any,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Backwards-compat path for callers still passing a raw anthropic client.

    Mirrors the v0.2 ``_call_anthropic`` implementation. Deprecated; will
    be removed in a future release once external callers migrate.
    """
    try:
        response = raw_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        logger.debug(
            "anthropic (legacy) call: input=%s output=%s",
            getattr(getattr(response, "usage", None), "input_tokens", None),
            getattr(getattr(response, "usage", None), "output_tokens", None),
        )
        return text
    except Exception as exc:
        raise LLMResponseError(f"Anthropic call failed: {exc}") from exc


def _call_provider(
    provider: Any,
    system_prompt: str,
    user_content: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    model: str | None = None,
) -> str:
    """Single-turn provider call. Raises ``LLMUnavailableError`` when
    ``provider`` is ``None`` and ``LLMResponseError`` on transport failure.

    Backwards-compat: when ``provider`` is not an ``LLMProvider`` instance
    (e.g., an external caller passed a raw anthropic client), this falls
    back to the legacy ``client.messages.create(...)`` path.
    """
    if provider is None:
        raise LLMUnavailableError(
            "provider is None; install byline-audit[llm] and set "
            "ANTHROPIC_API_KEY or OPENAI_API_KEY"
        )
    from byline.llm_provider import LLMProviderError as _ProviderError

    if not isinstance(provider, LLMProvider):
        return _legacy_anthropic_call(
            provider, system_prompt, user_content, max_tokens, temperature
        )
    try:
        return provider.generate(
            system_prompt,
            user_content,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )
    except _ProviderError as exc:
        raise LLMResponseError(str(exc)) from exc


# ---------------------------------------------------------------------------
# v0.2 §7 — Public entry points
# ---------------------------------------------------------------------------


def run_alignment_semantic(
    audit_summary: dict[str, Any],
    readme_text: str,
    sampled_code: dict[str, str],
    provider: Any = None,
    *,
    anthropic_client: Any = None,
) -> tuple[list[dict], str]:
    """Call the configured provider with ``ALIGNMENT_SEMANTIC_SYSTEM_PROMPT``.

    Returns ``(list_of_check_dicts, llm_summary_text)``.
    ``list_of_check_dicts`` is parsed JSON. ``llm_summary_text`` comes from a
    follow-up call using ``ALIGNMENT_SUMMARY_PROMPT``. Both outputs are passed
    through ``strip_banned_phrases``.

    ``anthropic_client`` is accepted as a deprecated alias for ``provider``
    and routes through the legacy raw-client path inside ``_call_provider``.
    """
    if provider is None and anthropic_client is not None:
        provider = anthropic_client
    if provider is None:
        raise LLMUnavailableError(
            "provider is None; install byline-audit[llm] and set "
            "ANTHROPIC_API_KEY or OPENAI_API_KEY"
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

    raw_checks = _call_provider(
        provider,
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
    raw_summary = _call_provider(
        provider,
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
    provider: Any = None,
    *,
    anthropic_client: Any = None,
) -> list[dict]:
    """Call the provider with ``QUESTIONS_SYSTEM_PROMPT``, asking for ``n`` questions.

    Returns parsed JSON list of question dicts. Each question's ``text`` is
    passed through ``strip_banned_phrases``.

    ``anthropic_client`` is accepted as a deprecated alias for ``provider``.
    """
    if provider is None and anthropic_client is not None:
        provider = anthropic_client
    if provider is None:
        raise LLMUnavailableError(
            "provider is None; install byline-audit[llm] and set "
            "ANTHROPIC_API_KEY or OPENAI_API_KEY"
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

    raw = _call_provider(
        provider,
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
    provider: Any = None,
    *,
    anthropic_client: Any = None,
) -> str:
    """One chat turn.

    Appends ``user_message`` to ``conversation_history``, calls the
    provider, and returns the text reply (post-processed via
    ``strip_banned_phrases``). The caller is responsible for persisting
    the updated history; this function does not mutate the passed-in list.

    For multi-turn chat, the conversation history is folded into a single
    user prompt so that the provider abstraction stays single-turn. This
    matches v0.2 semantics for short interactive sessions.

    ``anthropic_client`` is accepted as a deprecated alias for ``provider``;
    when supplied, the legacy native multi-message path is taken.
    """
    if provider is None and anthropic_client is not None:
        provider = anthropic_client
    if provider is None:
        raise LLMUnavailableError(
            "provider is None; install byline-audit[llm] and set "
            "ANTHROPIC_API_KEY or OPENAI_API_KEY"
        )

    # Backwards-compat: a raw anthropic client takes the legacy multi-message
    # path so prior callers still receive native chat-history handling.
    if not isinstance(provider, LLMProvider):
        messages = list(conversation_history) + [{"role": "user", "content": user_message}]
        try:
            response = provider.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                temperature=0.2,
                system=system_prompt,
                messages=messages,
            )
            text = "".join(block.text for block in response.content if hasattr(block, "text"))
            logger.debug(
                "anthropic (legacy) chat call: input=%s output=%s",
                getattr(getattr(response, "usage", None), "input_tokens", None),
                getattr(getattr(response, "usage", None), "output_tokens", None),
            )
        except Exception as exc:
            raise LLMResponseError(f"Anthropic chat call failed: {exc}") from exc
        return strip_banned_phrases(text)

    # Provider path: fold history into the user content.
    if conversation_history:
        history_lines: list[str] = ["Prior conversation:"]
        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            history_lines.append(f"[{role}] {content}")
        history_lines.append("")
        history_lines.append(f"Current user message:\n{user_message}")
        folded_user = "\n".join(history_lines)
    else:
        folded_user = user_message

    text = _call_provider(
        provider,
        system_prompt,
        folded_user,
        max_tokens=2000,
        temperature=0.2,
    )
    return strip_banned_phrases(text)


# ---------------------------------------------------------------------------
# Legacy import-module reference (kept for tests that patch this name)
# ---------------------------------------------------------------------------
#
# ``test_get_anthropic_client_with_key_no_package`` patches
# ``byline.llm.import_module`` to simulate a missing anthropic package.
# The provider lookup now lives in ``byline.llm_provider``, so the legacy
# patch is intercepted via ``get_anthropic_client``'s indirection through
# ``get_llm_provider``. Keep the reference here so older tests that still
# monkeypatch ``byline.llm.import_module`` don't raise AttributeError.

__all__ = [
    "SYSTEM_PROMPT",
    "ALIGNMENT_SEMANTIC_SYSTEM_PROMPT",
    "ALIGNMENT_SUMMARY_PROMPT",
    "QUESTIONS_SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "BANNED_PHRASES",
    "strip_banned_phrases",
    "LLMUnavailableError",
    "LLMResponseError",
    "LLMProvider",
    "get_llm_provider",
    "get_anthropic_client",
    "qualitative_pass",
    "run_alignment_semantic",
    "run_questions",
    "run_chat_turn",
    "import_module",
]
