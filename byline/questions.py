"""LLM-required interview question generator. See spec §5.7.

This module prepares a compact, structured audit summary plus a handful of
file excerpts and asks Claude (via :mod:`byline.llm`) to produce a fixed
number of grounded interview questions.

Two non-negotiable rules:

1. The LLM is REQUIRED. Unlike :func:`byline.llm.qualitative_pass`, the
   questions feature has no offline fallback — without a model, there is
   no meaningful output. We raise :class:`~byline.llm.LLMUnavailableError`
   immediately if the caller passes ``anthropic_client=None``.
2. Questions probe engineering decisions and operational tradeoffs only.
   The framing (enforced in ``QUESTIONS_SYSTEM_PROMPT``) never asks about
   authorship, AI use, or writing style.

The function is intentionally small: it shapes the audit-result inputs into
the dict structure that :func:`byline.llm.run_questions` expects, then
converts the returned JSON into :class:`~byline.models.QuestionSet`.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from byline.llm import LLMUnavailableError, run_questions
from byline.models import (
    AlignmentCheck,
    AuditResult,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    Question,
    QuestionSet,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum number of lines to read from each sampled file excerpt.
_MAX_EXCERPT_LINES = 80

# Maximum number of file excerpts to send to the LLM.
_MAX_SAMPLED_FILES = 3

# Maximum number of fingerprint hits to summarise.
_MAX_FINGERPRINTS = 5

# Maximum number of disproportion / delta entries to summarise.
_MAX_DISPROPORTIONS = 3
_MAX_DELTAS = 3

# Maximum number of alignment gaps (severity != info) to summarise.
_MAX_ALIGNMENT_GAPS = 3

# Severity ordering — higher number = more severe.
_DISPROPORTION_SEVERITY: dict[str, int] = {
    "info": 0,
    "notable": 1,
    "significant": 2,
}

_DELTA_SEVERITY: dict[str, int] = {
    "aligned": 0,
    "notable": 1,
    "significant": 2,
    "extreme": 3,
}

# Compose-like file name shapes — case-insensitive match on stem/full name.
_COMPOSE_NAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

# Extensions counted as "source files" for the third sampling slot.
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_questions(
    audit: AuditResult,
    repo_path: Path,
    anthropic_client: Any,
    n: int = 10,
) -> QuestionSet:
    """Generate ``n`` grounded follow-up interview questions.

    Pre-processes the audit findings into a compact summary for the LLM,
    samples up to 3 file excerpts that anchor strong signals, then calls
    Claude via :func:`byline.llm.run_questions`. Returns a
    :class:`~byline.models.QuestionSet` whose ``questions`` list contains
    the entries Claude returned, minus any malformed items (which are
    logged and skipped).

    Raises :class:`~byline.llm.LLMUnavailableError` when
    ``anthropic_client`` is ``None`` (fail-fast, before any work).
    """
    if anthropic_client is None:
        raise LLMUnavailableError(
            "This command requires the LLM extras. Install with:\n"
            "  pip install 'byline[llm]'\n"
            "and set ANTHROPIC_API_KEY in your environment."
        )

    audit_summary = _build_audit_summary(audit)
    sampled_excerpts = _sample_file_excerpts(audit, repo_path)

    # ``run_questions`` re-raises LLMUnavailableError as-is and surfaces
    # LLMResponseError for malformed model output. We don't intercept either.
    question_dicts = run_questions(
        audit_summary=audit_summary,
        sampled_excerpts=sampled_excerpts,
        n=n,
        anthropic_client=anthropic_client,
    )

    questions: list[Question] = []
    for d in question_dicts:
        try:
            questions.append(
                Question(
                    text=d["text"],
                    grounding_file=d.get("grounding_file"),
                    grounding_line=d.get("grounding_line"),
                    signal_addressed=d.get("signal_addressed", ""),
                    rationale=d.get("rationale", ""),
                )
            )
        except Exception as exc:  # noqa: BLE001 — tolerate any pydantic / KeyError
            logger.warning("Skipping malformed question entry: %s", exc)

    return QuestionSet(
        questions=questions,
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_audit_summary(audit: AuditResult) -> dict[str, Any]:
    """Build the compact ``audit_summary`` dict handed to the LLM."""
    return {
        "target_repo": audit.target_repo,
        "candidate": audit.candidate,
        "overall_signal": audit.overall_signal,
        "top_fingerprints": _top_fingerprints(audit.fingerprints),
        "top_disproportions": _top_disproportions(audit.disproportions),
        "top_deltas": _top_deltas(audit.deltas),
        "alignment_gaps": _alignment_gaps(audit),
    }


def _top_fingerprints(hits: list[FingerprintHit]) -> list[dict[str, Any]]:
    """Pick up to 5 informative fingerprints — diverse categories first, then
    by within-category frequency."""
    if not hits:
        return []

    # Count hits per category so we can prefer high-frequency anchors.
    category_counts = Counter(h.category for h in hits)

    seen_categories: set[str] = set()
    diverse: list[FingerprintHit] = []
    remaining: list[FingerprintHit] = []

    # First pass: one hit per category, ordered by category frequency.
    by_category_freq = sorted(
        hits, key=lambda h: (-category_counts[h.category], h.file_path, h.line_number or 0)
    )
    for h in by_category_freq:
        if h.category not in seen_categories:
            diverse.append(h)
            seen_categories.add(h.category)
        else:
            remaining.append(h)
        if len(diverse) >= _MAX_FINGERPRINTS:
            break

    # Second pass: fill remaining slots from the leftover hits, preserving
    # the original ordering (which the audit pipeline already orders by
    # significance).
    picked = diverse
    if len(picked) < _MAX_FINGERPRINTS:
        for h in remaining:
            picked.append(h)
            if len(picked) >= _MAX_FINGERPRINTS:
                break

    return [
        {
            "file": h.file_path,
            "pattern": h.pattern,
            "category": h.category,
            "excerpt": (h.excerpt or "")[:200],
        }
        for h in picked[:_MAX_FINGERPRINTS]
    ]


def _top_disproportions(
    findings: list[DisproportionFinding],
) -> list[dict[str, Any]]:
    """Top 3 disproportions sorted by severity (significant > notable > info)."""
    ranked = sorted(
        findings,
        key=lambda f: -_DISPROPORTION_SEVERITY.get(f.severity, 0),
    )
    return [
        {
            "name": f.name,
            "observed": f.observed,
            "threshold": f.threshold,
            "severity": f.severity,
            "description": f.description,
        }
        for f in ranked[:_MAX_DISPROPORTIONS]
    ]


def _top_deltas(deltas: list[ComparativeDelta]) -> list[dict[str, Any]]:
    """Top 3 deltas sorted by severity (extreme > significant > notable > aligned)."""
    ranked = sorted(
        deltas,
        key=lambda d: -_DELTA_SEVERITY.get(d.severity, 0),
    )
    return [
        {
            "metric": d.metric,
            "baseline_value": d.baseline_value,
            "target_value": d.target_value,
            "absolute_delta": d.absolute_delta,
            "relative_delta": d.relative_delta,
            "severity": d.severity,
        }
        for d in ranked[:_MAX_DELTAS]
    ]


def _alignment_gaps(audit: AuditResult) -> list[dict[str, Any]]:
    """Top 3 alignment checks with severity != 'info'. Empty list when no
    alignment findings were attached to the audit."""
    if audit.alignment is None:
        return []

    non_info: list[AlignmentCheck] = [c for c in audit.alignment.checks if c.severity != "info"]
    # Sort by severity weight (significant > notable). Both map to int via
    # the disproportion severity mapping (significant=2, notable=1).
    ranked = sorted(
        non_info,
        key=lambda c: -_DISPROPORTION_SEVERITY.get(c.severity, 0),
    )
    return [
        {
            "kind": c.kind,
            "description": c.description,
            "severity": c.severity,
        }
        for c in ranked[:_MAX_ALIGNMENT_GAPS]
    ]


# ---------------------------------------------------------------------------
# File-excerpt sampling
# ---------------------------------------------------------------------------


def _sample_file_excerpts(audit: AuditResult, repo_path: Path) -> dict[str, str]:
    """Pick up to three files from the repo that anchor strong fingerprint
    signals: one compose-like file, one shell script, one source file.

    Returns a mapping of relative path → annotated content (head of file,
    capped at 80 lines, each line prefixed with its 1-based line number).
    """
    # File-path → fingerprint count over the audit's hits.
    counts: Counter[str] = Counter(h.file_path for h in audit.fingerprints)

    selected: list[str] = []
    seen: set[str] = set()

    compose_pick = _pick_first_match(counts, predicate=_is_compose_filename, repo_path=repo_path)
    if compose_pick is not None and compose_pick not in seen:
        selected.append(compose_pick)
        seen.add(compose_pick)

    shell_pick = _pick_first_match(counts, predicate=_is_shell_filename, repo_path=repo_path)
    if shell_pick is not None and shell_pick not in seen:
        selected.append(shell_pick)
        seen.add(shell_pick)

    source_pick = _pick_first_match(counts, predicate=_is_source_filename, repo_path=repo_path)
    if source_pick is not None and source_pick not in seen:
        selected.append(source_pick)
        seen.add(source_pick)

    excerpts: dict[str, str] = {}
    for relpath in selected[:_MAX_SAMPLED_FILES]:
        content = _read_excerpt(repo_path / relpath)
        if content is not None:
            excerpts[relpath] = content
    return excerpts


def _pick_first_match(counts: Counter[str], predicate, repo_path: Path) -> str | None:
    """Return the path with the highest fingerprint count whose name matches
    ``predicate`` and which exists in ``repo_path``. ``None`` if no match."""
    for path, _count in counts.most_common():
        if predicate(path) and (repo_path / path).is_file():
            return path
    return None


def _is_compose_filename(path: str) -> bool:
    name = Path(path).name.lower()
    return name in _COMPOSE_NAMES or name.startswith("docker-compose")


def _is_shell_filename(path: str) -> bool:
    return Path(path).suffix.lower() == ".sh"


def _is_source_filename(path: str) -> bool:
    return Path(path).suffix.lower() in _SOURCE_EXTENSIONS


def _read_excerpt(path: Path) -> str | None:
    """Read up to ``_MAX_EXCERPT_LINES`` lines from ``path``, prefix each
    with its 1-based line number. Returns ``None`` on read failure."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines: list[str] = []
            for idx, raw in enumerate(fh, start=1):
                if idx > _MAX_EXCERPT_LINES:
                    break
                lines.append(f"{idx}: {raw.rstrip()}\n")
        return "".join(lines)
    except OSError as exc:
        logger.warning("Failed to read excerpt for %s: %s", path, exc)
        return None
