"""Optional Anthropic-backed qualitative commentary pass. See spec §10.

The LLM is asked to *interpret* comparative signals, never to determine
authorship. All output language must use words like "divergence", "signals",
"indicators", and "comparative analysis". The module is a no-op (returns
``None``) when ``ANTHROPIC_API_KEY`` is unset or the ``anthropic`` package
is not installed — failure to call the LLM never crashes an audit.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from importlib import import_module

from byline.models import AuditResult

logger = logging.getLogger(__name__)


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
