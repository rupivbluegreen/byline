"""Comparative scoring + audit orchestration. See spec §8.

This module combines the prose/shell stylistic signals from
:mod:`byline.metrics`, the catalogued patterns from :mod:`byline.fingerprints`,
and the structural ratios from :mod:`byline.disproportion` into a single
:class:`~byline.models.AuditResult`. Every output here is framed as
*comparative divergence* between a candidate's prior writing surface (the
baseline) and the target repository's surface — never as a verdict on
authorship.

The orchestrator :func:`audit` handles both local-filesystem targets and
GitHub URLs. For URLs, v0.1 avoids a full ``git clone`` and instead fetches
the repository tree via the GitHub REST API, streaming relevant files into a
temporary directory before falling through to the local-path branch.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Literal

from byline.corpus import build_corpus
from byline.disproportion import analyze as analyze_disproportions
from byline.fingerprints import scan_repo
from byline.github_client import get_file_content, get_repo_tree
from byline.metrics import metrics_for_text
from byline.models import (
    AlignmentFindings,
    AuditResult,
    BaselineCorpus,
    BoilerplateFinding,
    ComparativeDelta,
    DisproportionFinding,
    FingerprintHit,
    HistoryFindings,
    SelfBaselineFinding,
    StyleProfile,
    VoiceFinding,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory names pruned from the target-profile walk. Mirrors the exclusion
# set used by the disproportion analyser so the two surfaces stay consistent.
_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
    }
)

# Skip files larger than this when computing the target style profile —
# matches the cap used by the fingerprint scanner.
_MAX_FILE_SIZE = 500 * 1024

# StyleProfile fields, in the order we emit deltas. Keep this in sync with
# :class:`byline.models.StyleProfile`.
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

# Sentinel returned for relative_delta when baseline is zero and target is
# non-zero. We deliberately avoid ``float('inf')`` so downstream JSON/Pydantic
# serialisation stays clean.
_ZERO_BASELINE_RELATIVE_SENTINEL = 10.0

# Source-code extensions worth pulling when materialising a remote target so
# the disproportion analyser sees a representative LOC count. Prose and shell
# are pulled regardless of this list.
_REMOTE_CODE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".c",
    ".h",
    ".cpp",
)


# ---------------------------------------------------------------------------
# Repo walking helpers
# ---------------------------------------------------------------------------


def _walk_repo(repo_path: Path) -> list[Path]:
    """Yield every non-excluded file under ``repo_path`` as a sorted list."""

    if not repo_path.exists() or not repo_path.is_dir():
        return []
    out: list[Path] = []
    for candidate in repo_path.rglob("*"):
        try:
            rel_parts = candidate.relative_to(repo_path).parts
        except ValueError:
            continue
        if any(part in _EXCLUDE_DIRS for part in rel_parts[:-1]):
            continue
        if not candidate.is_file():
            continue
        out.append(candidate)
    return sorted(out)


def _read_text_safely(path: Path) -> str | None:
    """Return file text, or None if the file is too large or undecodable."""

    try:
        if path.stat().st_size > _MAX_FILE_SIZE:
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Baseline + target profile computation
# ---------------------------------------------------------------------------


def compute_baseline_profile(corpus: BaselineCorpus) -> StyleProfile:
    """Aggregate the corpus prose into one :class:`StyleProfile`.

    Concatenates every ``corpus.samples[*].text`` with a blank-line separator
    and runs :func:`byline.metrics.metrics_for_text` over the result with
    ``kind="prose"``. Returns a zero-valued profile when the corpus is empty.
    """

    if not corpus.samples:
        return metrics_for_text("", kind="prose")
    joined = "\n\n".join(sample.text for sample in corpus.samples)
    return metrics_for_text(joined, kind="prose")


def compute_target_profile(repo_path: Path) -> StyleProfile:
    """Compute a :class:`StyleProfile` over the target repo's writing surface.

    Walks ``repo_path``, gathers every ``*.md`` file as the prose surface and
    every ``*.sh`` file plus ``Dockerfile`` as the shell surface, joins each
    family with blank-line separators, and computes the two corresponding
    profiles. The returned profile takes its prose signals from the prose
    profile and its ``banner_comment_density`` / ``progress_ux_score`` from
    the shell profile. Empty or missing repos return an all-zero profile.
    """

    repo_path = Path(repo_path)
    prose_chunks: list[str] = []
    shell_chunks: list[str] = []

    for path in _walk_repo(repo_path):
        suffix = path.suffix.lower()
        name_lower = path.name.lower()
        text: str | None
        if suffix == ".md":
            text = _read_text_safely(path)
            if text:
                prose_chunks.append(text)
        elif suffix == ".sh" or name_lower == "dockerfile":
            text = _read_text_safely(path)
            if text:
                shell_chunks.append(text)

    prose_text = "\n\n".join(prose_chunks)
    shell_text = "\n\n".join(shell_chunks)

    prose_profile = metrics_for_text(prose_text, kind="prose")
    shell_profile = metrics_for_text(shell_text, kind="shell")

    return StyleProfile(
        em_dash_density=prose_profile.em_dash_density,
        emoji_in_headers_ratio=prose_profile.emoji_in_headers_ratio,
        avg_sentence_length=prose_profile.avg_sentence_length,
        type_token_ratio=prose_profile.type_token_ratio,
        typo_rate=prose_profile.typo_rate,
        sophistication_score=prose_profile.sophistication_score,
        banner_comment_density=shell_profile.banner_comment_density,
        progress_ux_score=shell_profile.progress_ux_score,
    )


# ---------------------------------------------------------------------------
# Deltas
# ---------------------------------------------------------------------------


def _classify_severity(
    relative_delta: float,
) -> Literal["aligned", "notable", "significant", "extreme"]:
    """Map an absolute relative-delta magnitude to a severity bucket.

    Boundaries (per spec §8):

    * ``< 0.25`` -> ``"aligned"``
    * ``< 0.75`` -> ``"notable"``
    * ``< 2.0``  -> ``"significant"``
    * otherwise  -> ``"extreme"``
    """

    magnitude = abs(relative_delta)
    if magnitude < 0.25:
        return "aligned"
    if magnitude < 0.75:
        return "notable"
    if magnitude < 2.0:
        return "significant"
    return "extreme"


def compute_deltas(baseline: StyleProfile, target: StyleProfile) -> list[ComparativeDelta]:
    """Per-metric comparison with severity classification.

    For each of the 8 :class:`StyleProfile` fields, produces a
    :class:`ComparativeDelta` with:

    * ``absolute_delta = target_value - baseline_value``
    * ``relative_delta = absolute_delta / baseline_value`` when
      ``baseline_value > 0``.
    * ``relative_delta = 0.0`` when both values are zero.
    * ``relative_delta = ±10.0`` (the zero-baseline sentinel) when the
      baseline is zero but the target is non-zero. The sentinel is used
      instead of ``float('inf')`` so the result serialises cleanly downstream.
    * Severity is derived from ``abs(relative_delta)`` via
      :func:`_classify_severity`.

    Results are returned in :class:`StyleProfile` field order.
    """

    deltas: list[ComparativeDelta] = []
    for field in _STYLE_PROFILE_FIELDS:
        baseline_value = float(getattr(baseline, field))
        target_value = float(getattr(target, field))
        absolute_delta = target_value - baseline_value

        if baseline_value > 0:
            relative_delta = absolute_delta / baseline_value
        elif target_value == 0:
            relative_delta = 0.0
        else:
            sign = 1.0 if target_value > 0 else -1.0
            relative_delta = sign * _ZERO_BASELINE_RELATIVE_SENTINEL

        severity = _classify_severity(relative_delta)
        deltas.append(
            ComparativeDelta(
                metric=field,
                baseline_value=baseline_value,
                target_value=target_value,
                absolute_delta=absolute_delta,
                relative_delta=relative_delta,
                severity=severity,
            )
        )
    return deltas


# ---------------------------------------------------------------------------
# Overall signal
# ---------------------------------------------------------------------------


_SIGNAL_LEVELS: tuple[Literal["aligned", "mixed", "divergent", "highly_divergent"], ...] = (
    "aligned",
    "mixed",
    "divergent",
    "highly_divergent",
)


def _v01_base_level(
    deltas: list[ComparativeDelta],
    fingerprints: list[FingerprintHit],
    disproportions: list[DisproportionFinding],
) -> Literal["aligned", "mixed", "divergent", "highly_divergent"]:
    """Compute the v0.1 base level from deltas, fingerprints, and disproportions.

    See :func:`overall_signal` for the decision table — this function is the
    pure v0.1 implementation, factored out so v0.2 additive adjustments can be
    layered on top.
    """

    extreme_deltas = sum(1 for d in deltas if d.severity == "extreme")
    significant_deltas = sum(1 for d in deltas if d.severity in ("significant", "extreme"))
    n_fp = len(fingerprints)
    n_signif_disp = sum(1 for x in disproportions if x.severity == "significant")
    n_notable_disp = sum(1 for x in disproportions if x.severity in ("notable", "significant"))

    if extreme_deltas >= 1 and n_fp >= 8 and n_signif_disp >= 1:
        return "highly_divergent"
    if significant_deltas >= 4 or n_fp >= 8 or extreme_deltas >= 1:
        return "divergent"
    if significant_deltas >= 2 or n_fp >= 4 or n_notable_disp >= 1:
        return "mixed"
    return "aligned"


def overall_signal(
    deltas: list[ComparativeDelta],
    fingerprints: list[FingerprintHit],
    disproportions: list[DisproportionFinding],
    *,
    history: HistoryFindings | None = None,
    alignment: AlignmentFindings | None = None,
    voice: VoiceFinding | None = None,
    boilerplate: BoilerplateFinding | None = None,
    self_baseline: SelfBaselineFinding | None = None,
) -> Literal["aligned", "mixed", "divergent", "highly_divergent"]:
    """Heuristic combination of v0.1 streams plus v0.2 findings into one label.

    The four levels (ordered from most aligned to most divergent) are::

        ["aligned", "mixed", "divergent", "highly_divergent"]

    **v0.1 base.** From ``deltas``, ``fingerprints``, and ``disproportions``::

        extreme_deltas      = len([d for d in deltas if d.severity == "extreme"])
        significant_deltas  = len([d for d in deltas
                                   if d.severity in {"significant", "extreme"}])
        n_fp                = len(fingerprints)
        n_signif_disp       = len([x for x in disproportions
                                   if x.severity == "significant"])
        n_notable_disp      = len([x for x in disproportions
                                   if x.severity in {"notable", "significant"}])

    Decision (first match wins):

    * ``extreme_deltas >= 1`` AND ``n_fp >= 8`` AND ``n_signif_disp >= 1``
      -> ``"highly_divergent"``
    * ``significant_deltas >= 4`` OR ``n_fp >= 8`` OR ``extreme_deltas >= 1``
      -> ``"divergent"``
    * ``significant_deltas >= 2`` OR ``n_fp >= 4`` OR ``n_notable_disp >= 1``
      -> ``"mixed"``
    * otherwise -> ``"aligned"``

    **v0.2 adjustments.** The base index in ``LEVELS`` is then shifted by an
    integer adjustment derived from the v0.2 findings:

    * ``history.timeline.bursty``                          -> ``+1``
    * ``history.timeline.first_commit_appears_pasted``     -> ``+1``
    * ``history.messages.self_baseline_divergence > 0.5``  -> ``+1``
    * ``history.identity.drift_detected``                  -> ``+2``
    * ``alignment.overall_alignment == "significant_gaps"`` -> ``+1``
    * ``not voice.has_first_person_voice``                 -> ``+1``
    * ``voice.ai_disclosure_found``                        -> ``-2``
      (disclosure shifts the comparative signal *toward* aligned)
    * ``boilerplate.severity == "significant"``            -> ``+1``
    * ``self_baseline.within_repo_divergence == "significant"`` -> ``+1``

    Any v0.2 argument left at ``None`` contributes nothing; with every v0.2
    argument ``None`` the function returns the v0.1 base level exactly. The
    final index is clamped to ``[0, len(LEVELS) - 1]`` so extreme adjustments
    never run off either end.
    """

    base_level = _v01_base_level(deltas, fingerprints, disproportions)
    base_idx = _SIGNAL_LEVELS.index(base_level)

    adj = 0
    if history is not None:
        if history.timeline.bursty:
            adj += 1
        if history.timeline.first_commit_appears_pasted:
            adj += 1
        if history.messages.self_baseline_divergence > 0.5:
            adj += 1
        if history.identity.drift_detected:
            adj += 2
    if alignment is not None:
        if alignment.overall_alignment == "significant_gaps":
            adj += 1
    if voice is not None:
        if not voice.has_first_person_voice:
            adj += 1
        if voice.ai_disclosure_found:
            adj -= 2
    if boilerplate is not None:
        if boilerplate.severity == "significant":
            adj += 1
    if self_baseline is not None:
        if self_baseline.within_repo_divergence == "significant":
            adj += 1

    final_idx = max(0, min(len(_SIGNAL_LEVELS) - 1, base_idx + adj))
    return _SIGNAL_LEVELS[final_idx]


# ---------------------------------------------------------------------------
# GitHub URL parsing + remote materialisation
# ---------------------------------------------------------------------------


def _parse_github_url(url: str) -> tuple[str, str]:
    """Parse ``url`` into ``(owner, repo)``.

    Supports the three forms documented in spec §8:

    * ``https://github.com/{owner}/{repo}`` (optional trailing ``/`` or ``.git``)
    * ``git@github.com:{owner}/{repo}.git``
    * ``github.com/{owner}/{repo}``

    Raises :class:`ValueError` if the URL doesn't match any supported form.
    """

    s = url.strip()
    # SSH form: git@github.com:owner/repo.git
    if s.startswith("git@github.com:"):
        tail = s[len("git@github.com:") :]
    elif s.startswith("https://github.com/"):
        tail = s[len("https://github.com/") :]
    elif s.startswith("http://github.com/"):
        tail = s[len("http://github.com/") :]
    elif s.startswith("github.com/"):
        tail = s[len("github.com/") :]
    else:
        raise ValueError(f"Unrecognised GitHub URL form: {url!r}")

    tail = tail.rstrip("/")
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    parts = tail.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Could not extract owner/repo from {url!r}")
    return parts[0], parts[1]


def _looks_like_github_url(target: str) -> bool:
    """True when ``target`` looks like a GitHub URL rather than a local path."""

    return (
        target.startswith("https://github.com/")
        or target.startswith("http://github.com/")
        or target.startswith("github.com/")
        or target.startswith("git@github.com:")
    )


def _materialise_github_repo(owner: str, repo: str, token: str | None, dest: Path) -> None:
    """Download a representative slice of a GitHub repo into ``dest``.

    Pulls every prose file (``*.md``), shell file (``*.sh``), ``Dockerfile``,
    YAML config (``*.yml`` / ``*.yaml``), and a sample of code-bearing
    extensions (Python, JS/TS, Go, Rust, Java, Ruby, C/C++). v0.1 strategy:
    avoid the cost of a full ``git clone`` by walking the tree via the GitHub
    REST API and writing each blob to ``dest`` preserving its relative path.
    """

    try:
        tree = get_repo_tree(owner, repo, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to fetch tree for %s/%s: %s", owner, repo, exc)
        return

    for entry in tree:
        path = entry.get("path", "")
        if not path:
            continue
        name_lower = path.rsplit("/", 1)[-1].lower()
        suffix = ""
        if "." in name_lower:
            suffix = "." + name_lower.rsplit(".", 1)[-1]

        is_prose = suffix == ".md"
        is_shell = suffix == ".sh"
        is_dockerfile = name_lower == "dockerfile"
        is_yaml = suffix in (".yml", ".yaml")
        is_code = suffix in _REMOTE_CODE_SUFFIXES
        if not (is_prose or is_shell or is_dockerfile or is_yaml or is_code):
            continue

        try:
            content = get_file_content(owner, repo, path, token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to fetch %s from %s/%s: %s", path, owner, repo, exc)
            continue
        if not content:
            continue

        out_path = dest / path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.warning("failed to write %s: %s", out_path, exc)


# ---------------------------------------------------------------------------
# audit — top-level orchestrator
# ---------------------------------------------------------------------------


def _is_local_target(target: str | Path) -> bool:
    """Return True if ``target`` should be treated as an on-disk path."""

    if isinstance(target, Path):
        return True
    return not _looks_like_github_url(target)


def _zero_profile() -> StyleProfile:
    """Return a :class:`StyleProfile` with every numeric field at 0.0."""

    return StyleProfile(
        em_dash_density=0.0,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=0.0,
        type_token_ratio=0.0,
        typo_rate=0.0,
        sophistication_score=0.0,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )


def _run_audit_for_local_path(
    target: str | Path,
    target_path: Path,
    candidate_username: str | None,
    github_token: str | None,
    with_llm: bool,
    with_history: bool,
) -> AuditResult:
    """Shared audit pipeline once we have a concrete on-disk ``target_path``.

    1. Builds the baseline corpus when a candidate username is supplied.
    2. Computes baseline and target profiles, deltas, fingerprints, and
       disproportion findings.
    3. Runs v0.2 forensics: history (gated by ``with_history``), voice,
       boilerplate, self-baseline, and alignment. Each module is wrapped in a
       try/except so a single failure leaves the corresponding field ``None``
       rather than aborting the audit.
    4. Combines all signal streams into an overall comparative label via
       :func:`overall_signal`.
    5. Optionally calls the LLM qualitative pass (v0.1 Task 12); a missing
       module is treated as "no commentary available" rather than an error.
    """

    baseline_corpus: BaselineCorpus | None = None
    if candidate_username:
        baseline_corpus = build_corpus(candidate_username, github_token)

    if baseline_corpus is not None:
        baseline_profile = compute_baseline_profile(baseline_corpus)
    else:
        baseline_profile = _zero_profile()

    target_profile = compute_target_profile(target_path)

    if baseline_corpus is not None:
        deltas = compute_deltas(baseline_profile, target_profile)
    else:
        deltas = []

    fingerprints = scan_repo(target_path)
    disproportions = analyze_disproportions(target_path)

    # ---- v0.2 forensics ------------------------------------------------------
    history: HistoryFindings | None = None
    if with_history:
        try:
            from byline.history import audit_history

            history = audit_history(target_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history forensics failed: %s", exc)
            history = None

    voice: VoiceFinding | None = None
    try:
        from byline.voice import analyze_voice

        voice = analyze_voice(target_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice analysis failed: %s", exc)
        voice = None

    boilerplate: BoilerplateFinding | None = None
    try:
        from byline.boilerplate import analyze_boilerplate

        boilerplate = analyze_boilerplate(target_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boilerplate analysis failed: %s", exc)
        boilerplate = None

    self_baseline: SelfBaselineFinding | None = None
    try:
        from byline.self_baseline import compute_self_baseline

        self_baseline = compute_self_baseline(target_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("self-baseline analysis failed: %s", exc)
        self_baseline = None

    # Alignment: deterministic always; semantic only when --with-llm AND key.
    alignment: AlignmentFindings | None = None
    try:
        from byline.alignment import check_alignment

        client = None
        if with_llm:
            try:
                from byline.llm import get_anthropic_client

                client = get_anthropic_client()
            except Exception as exc:  # noqa: BLE001
                logger.warning("anthropic client unavailable: %s", exc)
                client = None
        alignment = check_alignment(
            target_path,
            with_llm=with_llm and client is not None,
            anthropic_client=client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("alignment analysis failed: %s", exc)
        alignment = None

    signal = overall_signal(
        deltas,
        fingerprints,
        disproportions,
        history=history,
        alignment=alignment,
        voice=voice,
        boilerplate=boilerplate,
        self_baseline=self_baseline,
    )

    llm_qualitative: str | None = None
    if with_llm:
        partial = AuditResult(
            target_repo=str(target),
            candidate=candidate_username,
            baseline=baseline_corpus,
            target_profile=target_profile,
            fingerprints=fingerprints,
            disproportions=disproportions,
            deltas=deltas,
            llm_qualitative=None,
            overall_signal=signal,
            history=history,
            alignment=alignment,
            voice=voice,
            boilerplate=boilerplate,
            self_baseline=self_baseline,
        )
        try:
            from byline.llm import qualitative_pass  # type: ignore[attr-defined]

            llm_qualitative = qualitative_pass(partial)
        except (ImportError, AttributeError):
            llm_qualitative = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM qualitative pass failed: %s", exc)
            llm_qualitative = None

    return AuditResult(
        target_repo=str(target),
        candidate=candidate_username,
        baseline=baseline_corpus,
        target_profile=target_profile,
        fingerprints=fingerprints,
        disproportions=disproportions,
        deltas=deltas,
        llm_qualitative=llm_qualitative,
        overall_signal=signal,
        history=history,
        alignment=alignment,
        voice=voice,
        boilerplate=boilerplate,
        self_baseline=self_baseline,
    )


def audit(
    target: str | Path,
    candidate_username: str | None,
    github_token: str | None,
    *,
    with_llm: bool = False,
    with_history: bool = True,
) -> AuditResult:
    """Run the full audit pipeline and return a populated :class:`AuditResult`.

    ``target`` may be either a local filesystem path (``Path`` or a string
    starting with ``/``, ``./``, ``.``, or anything that isn't a recognised
    GitHub URL form) or a GitHub URL (``https://github.com/owner/repo``,
    ``git@github.com:owner/repo.git``, or ``github.com/owner/repo``).

    For URL targets, v0.1 deliberately avoids a full ``git clone``. Instead,
    the repository tree is fetched via the GitHub REST API and a curated set
    of files (prose, shell, Dockerfile, YAML, and common code extensions) is
    written into a temporary directory before the local-path pipeline runs.

    When ``candidate_username`` is provided, a baseline corpus is assembled
    from the candidate's prior writing surface via
    :func:`byline.corpus.build_corpus`. Without a candidate, no baseline is
    built and the ``deltas`` field is empty.

    ``with_llm=True`` opportunistically invokes :func:`byline.llm.qualitative_pass`
    (v0.1 Task 12) and, when an Anthropic client is available, runs the
    semantic-alignment pass alongside the deterministic alignment checks. A
    missing or failing LLM pass leaves ``llm_qualitative`` as ``None`` rather
    than aborting the audit.

    ``with_history=True`` (the default) runs commit-history forensics via
    :func:`byline.history.audit_history`. Pass ``with_history=False`` to skip
    that step (e.g. when the target lacks a ``.git`` directory or when speed
    matters), leaving ``result.history`` as ``None``.
    """

    if _is_local_target(target):
        target_path = Path(target)
        return _run_audit_for_local_path(
            target,
            target_path,
            candidate_username,
            github_token,
            with_llm,
            with_history,
        )

    # Remote (GitHub URL) target — materialise into a tempdir then proceed.
    owner, repo = _parse_github_url(str(target))
    with tempfile.TemporaryDirectory(prefix="byline-") as tmpdir:
        dest = Path(tmpdir)
        _materialise_github_repo(owner, repo, github_token, dest)
        return _run_audit_for_local_path(
            target,
            dest,
            candidate_username,
            github_token,
            with_llm,
            with_history,
        )
