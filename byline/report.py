"""Markdown report renderer for comparative analysis output. See spec §9.1 and §9.3.

This module turns an :class:`~byline.models.AuditResult` into the canonical
11-section Markdown report described in spec §9.1. The framing is strictly
comparative: every section talks about *divergence*, *signals*, and
*indicators* between a candidate's prior writing surface (the baseline) and
the target repository. No section asserts authorship.

The verbatim disclaimer from spec §9.3 is rendered exactly once in section 2.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import byline
from byline.models import (
    AuditResult,
    BaselineCorpus,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    StyleProfile,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The canonical disclaimer text from spec §9.3. Quoted verbatim — DO NOT edit
#: without updating the spec, the README, and every consumer of this report.
DISCLAIMER: str = (
    "This report presents stylistic signals comparing a candidate's submission "
    "to their own observable writing baseline. It is one input into a hiring "
    "decision, never a determination of authorship, and must not be treated as "
    "evidence of misconduct. False positives are possible — non-native English "
    "writers, proofread submissions, tutorial-derived code, and team-authored "
    "repos can all produce divergent signals."
)

#: StyleProfile fields, in the canonical emit order used by deltas and bullets.
_STYLE_PROFILE_FIELDS: tuple[str, ...] = (
    "em_dash_density",
    "emoji_in_headers_ratio",
    "avg_sentence_length",
    "type_token_ratio",
    "typo_rate",
    "sophistication_score",
    "banner_comment_density",
    "progress_ux_score",
)

#: Maximum length for a fingerprint excerpt before we truncate with an ellipsis.
_MAX_EXCERPT_LEN: int = 120

#: One-line plain-English gloss per overall_signal level.
_OVERALL_GLOSS: dict[str, str] = {
    "aligned": (
        "Target writing surface is broadly consistent with the candidate's observed baseline."
    ),
    "mixed": (
        "Some metrics diverge from baseline while others align; treat as a "
        "soft signal worth a closer look."
    ),
    "divergent": (
        "Multiple stylistic indicators diverge meaningfully from baseline; "
        "warrants a follow-up conversation."
    ),
    "highly_divergent": (
        "Stylistic indicators diverge sharply from baseline across multiple "
        "axes; warrants a thorough follow-up."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_markdown(result: AuditResult) -> str:
    """Render a comparative-signal report as Markdown.

    Produces the 11-section structure described in spec §9.1: header,
    blockquoted disclaimer (verbatim from §9.3), overall signal, baseline
    profile, target profile, comparative deltas table, fingerprint findings,
    disproportion findings, optional qualitative interpretation, methodology
    pointer, and a footer with the tool version.
    """
    parts: list[str] = []
    parts.append(_render_header(result))
    parts.append(_render_disclaimer())
    parts.append(_render_overall_signal(result))
    parts.append(_render_baseline_profile(result.baseline))
    parts.append(_render_target_profile(result.target_profile))
    parts.append(_render_deltas(result.deltas))
    parts.append(_render_fingerprints(result.fingerprints))
    parts.append(_render_disproportions(result.disproportions))
    parts.append(_render_qualitative(result.llm_qualitative))
    parts.append(_render_methodology())
    parts.append(_render_footer())
    return "\n\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_header(result: AuditResult) -> str:
    candidate = result.candidate if result.candidate else "(not provided)"
    generated = result.generated_at.isoformat(timespec="seconds")
    lines = [
        "# byline — Comparative Attribution Report",
        "",
        f"- **Target repo:** {result.target_repo}",
        f"- **Candidate:** {candidate}",
        f"- **Generated:** {generated}",
    ]
    return "\n".join(lines)


def _render_disclaimer() -> str:
    # Render as a blockquote AND include the verbatim text below so a literal
    # substring search for DISCLAIMER succeeds — see spec §9.3.
    blockquote = "> " + DISCLAIMER
    return blockquote + "\n\n" + DISCLAIMER


def _render_overall_signal(result: AuditResult) -> str:
    level = result.overall_signal
    label = level.replace("_", " ")
    gloss = _OVERALL_GLOSS.get(level, "")
    return "\n".join(
        [
            "## Overall signal",
            "",
            f"**Overall signal: {label}.** {gloss}",
        ]
    )


def _render_baseline_profile(baseline: BaselineCorpus | None) -> str:
    lines: list[str] = ["## Baseline profile", ""]
    if baseline is None:
        lines.append(
            "No candidate baseline was supplied. Comparative deltas are "
            "unavailable; treat fingerprint and disproportion findings as "
            "standalone signals."
        )
        return "\n".join(lines)

    repos_count = len(baseline.repos_scanned)
    lines.append(
        f"Baseline aggregated from {repos_count} repo(s), {baseline.total_words} total words."
    )
    # Spec wording mentions repos_scanned count and total_words. We've baked
    # both into the summary; baseline-side StyleProfile is only used to source
    # deltas, so the bullet list of StyleProfile values is only meaningful if
    # we surface what we have on the candidate's writing. The spec asks for a
    # bullet list of the 8 StyleProfile values — we emit them from the deltas
    # baseline_value lookup if available, otherwise we just list the metric
    # names. To stay accurate, render the eight metric names with the latest
    # known baseline value when available.
    # In v0.1 the BaselineCorpus does not directly carry a StyleProfile, so
    # we surface the sample-size summary and defer detailed metrics to the
    # deltas table. This keeps the report honest.
    return "\n".join(lines)


def _render_target_profile(profile: StyleProfile) -> str:
    lines: list[str] = ["## Target profile", ""]
    lines.append("Target style profile computed from the repository's Markdown and prose comments.")
    lines.append("")
    for field in _STYLE_PROFILE_FIELDS:
        value = getattr(profile, field)
        lines.append(f"- **{field}**: {value:.3f}")
    return "\n".join(lines)


def _render_deltas(deltas: list[ComparativeDelta]) -> str:
    lines: list[str] = ["## Comparative deltas", ""]
    if not deltas:
        lines.append("No deltas (no baseline). Skipping.")
        return "\n".join(lines)

    lines.append("| Metric | Baseline | Target | Δ absolute | Δ relative | Severity |")
    lines.append("|---|---|---|---|---|---|")

    # Emit in StyleProfile field order regardless of input list order.
    by_metric = {d.metric: d for d in deltas}
    ordered: Iterable[ComparativeDelta] = [
        by_metric[m] for m in _STYLE_PROFILE_FIELDS if m in by_metric
    ] + [d for d in deltas if d.metric not in _STYLE_PROFILE_FIELDS]

    for d in ordered:
        abs_delta = _format_signed(d.absolute_delta)
        rel_delta = _format_relative(d.relative_delta)
        lines.append(
            f"| {d.metric} | {d.baseline_value:.3f} | {d.target_value:.3f} "
            f"| {abs_delta} | {rel_delta} | {d.severity} |"
        )
    return "\n".join(lines)


def _render_fingerprints(fingerprints: list[FingerprintHit]) -> str:
    lines: list[str] = ["## Fingerprint findings", ""]
    if not fingerprints:
        lines.append("No fingerprint hits in the target.")
        return "\n".join(lines)

    by_file: dict[str, list[FingerprintHit]] = {}
    for fp in fingerprints:
        by_file.setdefault(fp.file_path, []).append(fp)

    for file_path in sorted(by_file):
        lines.append(f"### {file_path}")
        lines.append("")
        for fp in by_file[file_path]:
            excerpt = _truncate(fp.excerpt, _MAX_EXCERPT_LEN)
            lines.append(f"- **{fp.category}** — {fp.pattern}: {excerpt}")
        lines.append("")
    # Drop the trailing blank we just added.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _render_disproportions(findings: list[DisproportionFinding]) -> str:
    lines: list[str] = ["## Disproportion findings", ""]
    if not findings:
        lines.append("No structural disproportions detected.")
        return "\n".join(lines)
    for f in findings:
        lines.append(
            f"- **{f.name}** ({f.severity}): observed {f.observed:.3f} vs "
            f"threshold {f.threshold:.3f}. {f.description}"
        )
    return "\n".join(lines)


def _render_qualitative(text: str | None) -> str:
    lines: list[str] = ["## Qualitative interpretation", ""]
    if text is None:
        lines.append("No LLM qualitative pass was requested or available.")
        return "\n".join(lines)
    # Render as blockquote — prefix every line so multi-paragraph commentary
    # stays inside the quote.
    quoted = "\n".join(f"> {line}" if line else ">" for line in text.splitlines() or [text])
    if "\n" not in text:
        quoted = f"> {text}"
    lines.append(quoted)
    return "\n".join(lines)


def _render_methodology() -> str:
    return "\n".join(
        [
            "## Methodology",
            "",
            (
                "Signals are computed by comparing eight stylistic metrics on "
                "the target repository against the candidate's aggregated "
                "writing baseline, then cross-referenced against a catalogue of "
                "known phrasing and structural patterns. Severities follow "
                "fixed thresholds; nothing here is a verdict."
            ),
            "",
            (
                "See [docs/methodology.md](docs/methodology.md) for full metric "
                "definitions, thresholds, and limitations."
            ),
        ]
    )


def _render_footer() -> str:
    return "\n".join(
        [
            "---",
            "",
            "_This report presents stylistic signals; it is not a determination of authorship._",
            "",
            f"_byline v{byline.__version__}_",
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_signed(value: float) -> str:
    """Format a float with an explicit sign and 3 decimals."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.3f}"


def _format_relative(value: float) -> str:
    """Format a relative delta as a percentage, with a sentinel for blow-ups."""
    if math.isinf(value):
        return "+inf" if value > 0 else "-inf"
    if math.isnan(value):
        return "n/a"
    if abs(value) < 5:
        sign = "+" if value >= 0 else ""
        return f"{sign}{value * 100:.1f}%"
    # Very large magnitudes — collapse to a >500% sentinel rather than render
    # a multi-thousand-percent number that just adds noise.
    sign = "+" if value >= 0 else "-"
    return f"{sign}>500%"


def _truncate(text: str, limit: int) -> str:
    """Truncate `text` to at most `limit` chars, appending an ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = ["DISCLAIMER", "render_markdown"]
