"""Commit-history forensics — comparative timeline signals. See spec §5.2.

This module surfaces "timeline signals" from a repository's git history:
who committed, when, in what cadence, with what messages, and how individual
files grew. Each function is a deterministic mapping from a git repo (or a
plain path that may not be a repo at all) to a structured finding.

Nothing in this module renders a verdict on authorship. The findings are
comparative-attribution signals that a downstream report can stack alongside
the stylistic and structural signals computed elsewhere in the package.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import git

from byline.metrics import metrics_for_text
from byline.models import (
    AuthorIdentityFinding,
    CommitMessageStyleFinding,
    CommitTimelineFinding,
    FileEvolutionFinding,
    HistoryFindings,
    StyleProfile,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BURST_WINDOW_SECONDS = 3600  # 1-hour sliding window
_BURST_DENSITY_THRESHOLD = 0.7
_BURST_MIN_COMMITS = 5

_PASTED_FILE_COUNT_THRESHOLD = 5
_PASTED_LOC_THRESHOLD = 200
_PASTED_HEAD_FRACTION = 0.7
_FILE_PASTED_FRACTION = 0.8

# Style-vector normalization scales — kept in sync with self_baseline.
_EM_DASH_SCALE = 100.0
_TYPO_SCALE = 50.0

_DEBUG_MESSAGE_RE = re.compile(
    r"\b(fix|typo|wip|oops|argh|broken|undo|revert|whoops|nit|tmp|hack)\b",
    re.IGNORECASE,
)

# Source-file extensions considered when picking "top largest" files for the
# audit's file-evolution sweep.
_SOURCE_EXTENSIONS: tuple[str, ...] = (".py", ".js", ".ts", ".go", ".rs")
_SHELL_EXTENSION = ".sh"


# ---------------------------------------------------------------------------
# Distance helper (mirrors byline.self_baseline._distance)
# ---------------------------------------------------------------------------


def _style_distance(a: StyleProfile, b: StyleProfile) -> float:
    """Euclidean distance between two profiles in the 4-D normalized style space.

    Mirrors :func:`byline.self_baseline._distance` so the within-repo divergence
    arithmetic is identical regardless of which module computes it.
    """

    va = (
        a.em_dash_density / _EM_DASH_SCALE,
        a.sophistication_score,
        a.typo_rate / _TYPO_SCALE,
        a.type_token_ratio,
    )
    vb = (
        b.em_dash_density / _EM_DASH_SCALE,
        b.sophistication_score,
        b.typo_rate / _TYPO_SCALE,
        b.type_token_ratio,
    )
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(va, vb, strict=True)))


# ---------------------------------------------------------------------------
# Empty / zeroed finding constructors
# ---------------------------------------------------------------------------


def _empty_timeline() -> CommitTimelineFinding:
    return CommitTimelineFinding(
        total_commits=0,
        span_seconds=0.0,
        burst_density=0.0,
        burst_window_start=None,
        bursty=False,
        first_commit_file_count=0,
        first_commit_loc=0,
        first_commit_appears_pasted=False,
    )


def _empty_messages() -> CommitMessageStyleFinding:
    empty_profile = metrics_for_text("", "prose")
    return CommitMessageStyleFinding(
        total_messages=0,
        avg_length_chars=0.0,
        style_profile=empty_profile,
        debug_commit_ratio=0.0,
        self_baseline_divergence=0.0,
    )


def _empty_identity() -> AuthorIdentityFinding:
    return AuthorIdentityFinding(
        unique_author_emails=[],
        unique_author_names=[],
        drift_detected=False,
    )


def _empty_file_evolution(file_path: str) -> FileEvolutionFinding:
    return FileEvolutionFinding(
        file_path=file_path,
        total_commits_touching=0,
        largest_single_addition_lines=0,
        appears_pasted=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_repo(repo_path: Path) -> git.Repo | None:
    """Open a ``git.Repo`` at ``repo_path`` or return ``None`` if not a git dir."""

    try:
        return git.Repo(str(repo_path))
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        logger.warning("history forensics unavailable — %s is not a git repo", repo_path)
        return None
    except Exception as exc:  # noqa: BLE001 — never block analysis on git errors
        logger.warning("history forensics could not open %s: %s", repo_path, exc)
        return None


def _commit_datetime(commit: git.Commit) -> datetime:
    """Convert a commit's ``committed_date`` epoch into a UTC ``datetime``."""

    return datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)


def _head_total_loc(repo: git.Repo) -> int:
    """Sum line counts across every file tracked at HEAD."""

    try:
        files = [p for p in repo.git.ls_files().splitlines() if p]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ls-files failed: %s", exc)
        return 0

    total = 0
    for rel in files:
        try:
            content = repo.git.show(f"HEAD:{rel}")
        except Exception:  # noqa: BLE001 — binary blob, deleted, etc.
            continue
        # ``git show`` strips the trailing newline; if the text is non-empty
        # treat it as at least one line.
        if not content:
            continue
        total += content.count("\n") + (0 if content.endswith("\n") else 1)
    return total


def _first_line(message: str | bytes) -> str:
    """Return the first line of a commit message, decoding bytes if needed."""

    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    if not message:
        return ""
    return message.splitlines()[0]


def _read_head_file(repo: git.Repo, rel_path: str) -> str:
    """Read ``rel_path`` from HEAD, returning ``""`` if missing."""

    try:
        return repo.git.show(f"HEAD:{rel_path}")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# analyze_timeline
# ---------------------------------------------------------------------------


def analyze_timeline(repo_path: Path) -> CommitTimelineFinding:
    """Burst-cadence and first-commit-paste signals from the commit timeline.

    * **Burst density**: the maximum count of commits inside any 1-hour sliding
      window, divided by the total commit count. A repo where most commits
      happen in a single short window will score near 1.0.
    * **First-commit-pasted**: True when the initial commit touched many files
      with many lines (``>= 5`` files AND ``>= 200`` insertions) or contributed
      more than 70 percent of the current HEAD line count.

    Non-git paths and empty repos return a zeroed finding.
    """

    repo = _open_repo(repo_path)
    if repo is None:
        return _empty_timeline()

    try:
        commits = list(repo.iter_commits())
    except Exception as exc:  # noqa: BLE001
        logger.warning("iter_commits failed for %s: %s", repo_path, exc)
        return _empty_timeline()

    total_commits = len(commits)
    if total_commits == 0:
        return _empty_timeline()

    # ``iter_commits`` yields newest first. Reverse to chronological order so
    # ``[0]`` is the initial commit and the sliding-window scan reads forward
    # in time.
    commits.reverse()

    span_seconds = float(commits[-1].committed_date - commits[0].committed_date)

    # ---- burst detection ------------------------------------------------
    times = [c.committed_date for c in commits]
    max_window_count = 1
    max_window_start_idx = 0
    if total_commits >= 2:
        # Two-pointer sweep — O(N) over sorted timestamps.
        right = 0
        for left in range(total_commits):
            if right < left:
                right = left
            while (
                right + 1 < total_commits and times[right + 1] - times[left] < _BURST_WINDOW_SECONDS
            ):
                right += 1
            window_count = right - left + 1
            if window_count > max_window_count:
                max_window_count = window_count
                max_window_start_idx = left

    burst_density = max_window_count / total_commits
    burst_window_start: datetime | None
    if total_commits < 2:
        burst_window_start = None
    else:
        burst_window_start = _commit_datetime(commits[max_window_start_idx])
    bursty = burst_density > _BURST_DENSITY_THRESHOLD and total_commits >= _BURST_MIN_COMMITS

    # ---- first-commit paste detection -----------------------------------
    first_commit = commits[0]
    try:
        stats_files = first_commit.stats.files
        first_commit_file_count = len(stats_files)
        first_commit_loc = int(first_commit.stats.total.get("insertions", 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("first-commit stats failed: %s", exc)
        first_commit_file_count = 0
        first_commit_loc = 0

    total_head_loc = _head_total_loc(repo)

    first_commit_appears_pasted = (
        first_commit_file_count >= _PASTED_FILE_COUNT_THRESHOLD
        and first_commit_loc >= _PASTED_LOC_THRESHOLD
    ) or (total_head_loc > 0 and first_commit_loc > _PASTED_HEAD_FRACTION * total_head_loc)

    return CommitTimelineFinding(
        total_commits=total_commits,
        span_seconds=span_seconds,
        burst_density=burst_density,
        burst_window_start=burst_window_start,
        bursty=bursty,
        first_commit_file_count=first_commit_file_count,
        first_commit_loc=first_commit_loc,
        first_commit_appears_pasted=first_commit_appears_pasted,
    )


# ---------------------------------------------------------------------------
# analyze_commit_messages
# ---------------------------------------------------------------------------


def analyze_commit_messages(repo_path: Path) -> CommitMessageStyleFinding:
    """Style signals from commit-message first lines vs. the README baseline.

    Merge commits are excluded — their messages are usually auto-generated and
    would dilute the author-voice signal. Distance between the concatenated
    commit-message profile and the README profile uses the same 4-D normalized
    vector employed by :mod:`byline.self_baseline`.
    """

    repo = _open_repo(repo_path)
    if repo is None:
        return _empty_messages()

    try:
        commits = list(repo.iter_commits())
    except Exception as exc:  # noqa: BLE001
        logger.warning("iter_commits failed for %s: %s", repo_path, exc)
        return _empty_messages()

    first_lines: list[str] = []
    for commit in commits:
        if len(commit.parents) > 1:
            continue
        line = _first_line(commit.message)
        if line:
            first_lines.append(line)

    total_messages = len(first_lines)
    if total_messages == 0:
        return _empty_messages()

    avg_length_chars = sum(len(line) for line in first_lines) / total_messages
    joined = "\n".join(first_lines)
    style_profile = metrics_for_text(joined, "prose")

    matching = sum(1 for line in first_lines if _DEBUG_MESSAGE_RE.search(line))
    debug_commit_ratio = matching / total_messages

    readme_text = _read_head_file(repo, "README.md")
    readme_profile = metrics_for_text(readme_text, "prose")
    self_baseline_divergence = _style_distance(style_profile, readme_profile)

    return CommitMessageStyleFinding(
        total_messages=total_messages,
        avg_length_chars=avg_length_chars,
        style_profile=style_profile,
        debug_commit_ratio=debug_commit_ratio,
        self_baseline_divergence=self_baseline_divergence,
    )


# ---------------------------------------------------------------------------
# analyze_identity
# ---------------------------------------------------------------------------


def analyze_identity(repo_path: Path) -> AuthorIdentityFinding:
    """Distinct author identities across the commit history.

    ``drift_detected`` is True when more than one unique email appears AND those
    emails span more than one distinct domain. Two emails on the same domain
    (e.g. ``alice@acme.com`` and ``alice@acme.com.bot``) do not trigger drift —
    they look like the same person across personal and bot accounts.
    """

    repo = _open_repo(repo_path)
    if repo is None:
        return _empty_identity()

    try:
        commits = list(repo.iter_commits())
    except Exception as exc:  # noqa: BLE001
        logger.warning("iter_commits failed for %s: %s", repo_path, exc)
        return _empty_identity()

    emails: set[str] = set()
    names: set[str] = set()
    for commit in commits:
        author = commit.author
        if author.email:
            emails.add(author.email.lower())
        if author.name:
            names.add(author.name)

    unique_emails = sorted(emails)
    unique_names = sorted(names)

    domains: set[str] = set()
    for email in unique_emails:
        if "@" in email:
            domains.add(email.split("@", 1)[1])

    drift_detected = len(unique_emails) > 1 and len(domains) > 1

    return AuthorIdentityFinding(
        unique_author_emails=unique_emails,
        unique_author_names=unique_names,
        drift_detected=drift_detected,
    )


# ---------------------------------------------------------------------------
# analyze_file_evolution
# ---------------------------------------------------------------------------


def analyze_file_evolution(repo_path: Path, file_path: str) -> FileEvolutionFinding:
    """Per-file growth signal: how much of ``file_path`` arrived in one commit.

    A file whose largest single-commit addition exceeds 80 percent of its
    current line count looks like it was pasted in wholesale rather than
    iterated on. Missing files (not present in HEAD) return a zeroed finding.
    """

    repo = _open_repo(repo_path)
    if repo is None:
        return _empty_file_evolution(file_path)

    head_content = _read_head_file(repo, file_path)
    if not head_content:
        return _empty_file_evolution(file_path)

    try:
        touching_commits = list(repo.iter_commits(paths=file_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("iter_commits(paths=%s) failed: %s", file_path, exc)
        return _empty_file_evolution(file_path)

    total_commits_touching = len(touching_commits)

    insertions_per_commit: list[int] = []
    for commit in touching_commits:
        try:
            stats = commit.stats.files.get(file_path, {})
            insertions_per_commit.append(int(stats.get("insertions", 0)))
        except Exception:  # noqa: BLE001
            continue

    largest_single_addition_lines = max(insertions_per_commit, default=0)
    current_total_lines = head_content.count("\n") + (0 if head_content.endswith("\n") else 1)
    if current_total_lines == 0:
        current_total_lines = 1
    appears_pasted = (
        largest_single_addition_lines / max(current_total_lines, 1) > _FILE_PASTED_FRACTION
    )

    return FileEvolutionFinding(
        file_path=file_path,
        total_commits_touching=total_commits_touching,
        largest_single_addition_lines=largest_single_addition_lines,
        appears_pasted=appears_pasted,
    )


# ---------------------------------------------------------------------------
# audit_history
# ---------------------------------------------------------------------------


def _candidate_files_for_evolution(repo: git.Repo) -> list[str]:
    """Pick the files to feed into ``analyze_file_evolution`` for an audit sweep.

    Includes ``README.md``, any ``docker-compose*.yml`` / ``.yaml`` files, the
    top 3 largest shell scripts, and the top 3 largest source files across the
    common languages (Python, JS/TS, Go, Rust). All paths returned are repo-
    relative and confirmed present at HEAD.
    """

    try:
        tracked = [p for p in repo.git.ls_files().splitlines() if p]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ls-files failed during audit: %s", exc)
        return []

    tracked_set = set(tracked)
    chosen: list[str] = []

    if "README.md" in tracked_set:
        chosen.append("README.md")

    compose_re = re.compile(r"(^|/)docker-compose[^/]*\.(yml|yaml)$", re.IGNORECASE)
    for path in tracked:
        if compose_re.search(path):
            chosen.append(path)

    def _loc(path: str) -> int:
        try:
            content = repo.git.show(f"HEAD:{path}")
        except Exception:  # noqa: BLE001
            return 0
        if not content:
            return 0
        return content.count("\n") + (0 if content.endswith("\n") else 1)

    shells = [p for p in tracked if p.lower().endswith(_SHELL_EXTENSION)]
    shells_by_size = sorted(shells, key=_loc, reverse=True)[:3]
    chosen.extend(shells_by_size)

    sources = [p for p in tracked if any(p.lower().endswith(ext) for ext in _SOURCE_EXTENSIONS)]
    sources_by_size = sorted(sources, key=_loc, reverse=True)[:3]
    chosen.extend(sources_by_size)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for path in chosen:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def audit_history(repo_path: Path) -> HistoryFindings:
    """Compose the four history sub-analyses into a single ``HistoryFindings``.

    The function is intentionally defensive: each sub-analysis is wrapped in a
    try/except so one corrupt commit or unreadable file cannot tank the whole
    audit. Non-git paths return a fully-zeroed result so callers can treat
    history forensics as always-present, even on plain directories.
    """

    repo_path = Path(repo_path)

    repo = _open_repo(repo_path)
    if repo is None:
        return HistoryFindings(
            timeline=_empty_timeline(),
            messages=_empty_messages(),
            identity=_empty_identity(),
            file_evolutions=[],
        )

    try:
        timeline = analyze_timeline(repo_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_timeline failed: %s", exc)
        timeline = _empty_timeline()

    try:
        messages = analyze_commit_messages(repo_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_commit_messages failed: %s", exc)
        messages = _empty_messages()

    try:
        identity = analyze_identity(repo_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_identity failed: %s", exc)
        identity = _empty_identity()

    file_evolutions: list[FileEvolutionFinding] = []
    try:
        candidates = _candidate_files_for_evolution(repo)
    except Exception as exc:  # noqa: BLE001
        logger.warning("file-evolution candidate selection failed: %s", exc)
        candidates = []

    for path in candidates:
        try:
            finding = analyze_file_evolution(repo_path, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyze_file_evolution failed for %s: %s", path, exc)
            continue
        if finding.total_commits_touching > 0:
            file_evolutions.append(finding)

    return HistoryFindings(
        timeline=timeline,
        messages=messages,
        identity=identity,
        file_evolutions=file_evolutions,
    )
