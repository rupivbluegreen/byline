"""Within-repo three-way style comparison. See spec §5.4.

A single repository often contains three distinct surfaces of writing produced
by the same author: commit messages, the README, and code comments. When the
author is one person writing consistently, these three surfaces tend to share
stylistic fingerprints — similar em-dash habits, similar lexical diversity,
similar typo patterns, similar vocabulary sophistication.

When the surfaces diverge sharply — README prose looks template-perfect,
comments look terse and human, commit messages look like a different person
altogether — that within-repo divergence is a comparative-attribution signal
worth surfacing. It is not a verdict on authorship; it is an observation that
the same repo contains stylistically inconsistent writing surfaces.

The module computes a :class:`StyleProfile` for each surface, projects it onto
a 4-dimensional normalized vector (em-dash density, sophistication, typo rate,
type-token ratio), and reports Euclidean distances between the three pairs.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import git

from byline.metrics import metrics_for_text
from byline.models import SelfBaselineFinding, StyleProfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directories that should be skipped when walking the repo for comments.
# Mirrors the exclude set used elsewhere in the codebase (boilerplate,
# disproportion) so the three modules behave consistently.
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
    }
)

# File extensions whose comment lines we extract. The comment-prefix tuples
# are the prefixes (after leading whitespace) that begin a comment line on
# that language. Python's docstrings are handled separately below.
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".sh": ("#",),
    ".bash": ("#",),
    ".zsh": ("#",),
    ".rb": ("#",),
    ".js": ("//",),
    ".ts": ("//",),
    ".jsx": ("//",),
    ".tsx": ("//",),
    ".c": ("//",),
    ".h": ("//",),
    ".cpp": ("//",),
    ".hpp": ("//",),
    ".java": ("//",),
    ".go": ("//",),
    ".rs": ("//",),
}

# Severity thresholds from the spec. The rule is applied to the *maximum*
# of the two reported distances — if either distance crosses a threshold,
# the overall classification reflects that.
_NOTABLE_DISTANCE = 0.3
_SIGNIFICANT_DISTANCE = 0.6

# Normalization scales for the 4-dimensional style vector. Each chosen so
# that a "very high" value of the underlying signal maps to roughly 1.0 in
# the normalized space, keeping all four dimensions comparable.
_EM_DASH_SCALE = 100.0  # 100 dashes per 1000 words -> 1.0
_TYPO_SCALE = 50.0  # 50 typos per 1000 words -> 1.0

_DivergenceLevel = Literal["consistent", "notable", "significant"]


# ---------------------------------------------------------------------------
# Internal helpers — distance math
# ---------------------------------------------------------------------------


def _normalized_vector(p: StyleProfile) -> tuple[float, float, float, float]:
    """Project a :class:`StyleProfile` onto the 4-dimensional normalized vector.

    Dimensions: ``em_dash_density / 100``, ``sophistication_score``,
    ``typo_rate / 50``, ``type_token_ratio``. The first and third are scaled
    so a saturating value lands near 1.0; the other two are already 0–1.
    """

    return (
        p.em_dash_density / _EM_DASH_SCALE,
        p.sophistication_score,
        p.typo_rate / _TYPO_SCALE,
        p.type_token_ratio,
    )


def _distance(a: StyleProfile, b: StyleProfile) -> float:
    """Euclidean distance in the 4-D normalized style space."""

    va, vb = _normalized_vector(a), _normalized_vector(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(va, vb, strict=True)))


def _classify(d1: float, d2: float) -> _DivergenceLevel:
    """Apply the severity rule to the larger of the two distances.

    The rule from the spec is "either distance crosses the threshold",
    which is equivalent to comparing the max of the two distances.
    """

    worst = max(d1, d2)
    if worst < _NOTABLE_DISTANCE:
        return "consistent"
    if worst < _SIGNIFICANT_DISTANCE:
        return "notable"
    return "significant"


def _label_for(distance: float) -> str:
    """Human-readable band for a single distance — used to build the note."""

    if distance < _NOTABLE_DISTANCE:
        return "aligned"
    if distance < _SIGNIFICANT_DISTANCE:
        return "diverge moderately"
    return "diverge significantly"


def _build_note(d1: float, d2: float) -> str:
    """One-line summary describing the two pairwise distances."""

    return (
        f"Commit messages and README {_label_for(d1)}; code comments and README {_label_for(d2)}."
    )


# ---------------------------------------------------------------------------
# Internal helpers — surface extraction
# ---------------------------------------------------------------------------


def _collect_commit_messages(repo_path: Path) -> str:
    """Concatenate first lines of all non-merge commits in the repo.

    Returns the empty string when ``repo_path`` is not a git repository
    (``InvalidGitRepositoryError`` / ``NoSuchPathError``) so the caller can
    operate on plain directories without special-casing.
    """

    try:
        repo = git.Repo(str(repo_path))
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return ""
    except Exception:  # noqa: BLE001 — defensive; never block analysis on git
        return ""

    lines: list[str] = []
    try:
        for commit in repo.iter_commits():
            # Skip merge commits — their messages are typically auto-generated
            # ("Merge branch ...") and would dilute the author-voice signal.
            if len(commit.parents) > 1:
                continue
            message = commit.message
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            first_line = message.splitlines()[0] if message else ""
            if first_line:
                lines.append(first_line)
    except Exception:  # noqa: BLE001 — empty repo, corrupt history, etc.
        return "\n".join(lines)

    return "\n".join(lines)


def _read_readme(repo_path: Path) -> str:
    """Read ``README.md`` at the repo root, returning ``""`` if absent."""

    readme = repo_path / "README.md"
    if not readme.is_file():
        return ""
    try:
        return readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _walk_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield source files under ``repo_path`` whose extension we know to comment-parse."""

    if not repo_path.exists() or not repo_path.is_dir():
        return

    for candidate in repo_path.rglob("*"):
        try:
            rel_parts = candidate.relative_to(repo_path).parts
        except ValueError:
            continue
        if any(part in _EXCLUDE_DIRS for part in rel_parts[:-1]):
            continue
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in _COMMENT_PREFIXES:
            continue
        yield candidate


def _extract_python_docstrings(text: str) -> list[str]:
    """Pull out the bodies of triple-quoted strings from a Python source file.

    The detection is intentionally simple: we scan for triple-double-quote and
    triple-single-quote delimiters and capture the text between matching pairs.
    This is not a full Python parser — it will include any triple-quoted string,
    not just docstrings — which is fine for stylistic signal aggregation.
    """

    bodies: list[str] = []
    for delim in ('"""', "'''"):
        pos = 0
        while True:
            start = text.find(delim, pos)
            if start == -1:
                break
            end = text.find(delim, start + len(delim))
            if end == -1:
                break
            body = text[start + len(delim) : end]
            if body.strip():
                bodies.append(body)
            pos = end + len(delim)
    return bodies


def _extract_comments_from_file(path: Path) -> str:
    """Return the concatenated comment text harvested from a single source file.

    For each language we strip the comment-prefix tokens (``#``, ``//``) and
    keep the remaining text. For Python we additionally include the contents
    of any triple-quoted strings, since module/class/function docstrings are
    the dominant prose surface in Python source.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    prefixes = _COMMENT_PREFIXES.get(path.suffix.lower(), ())
    if not prefixes:
        return ""

    out_lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        for prefix in prefixes:
            if stripped.startswith(prefix):
                # Drop the prefix itself (and one optional space after it)
                # so the recovered text reads like normal prose.
                body = stripped[len(prefix) :]
                if body.startswith(" "):
                    body = body[1:]
                if body:
                    out_lines.append(body)
                break

    pieces = ["\n".join(out_lines)] if out_lines else []
    if path.suffix.lower() == ".py":
        pieces.extend(_extract_python_docstrings(text))

    return "\n".join(p for p in pieces if p)


def _collect_code_comments(repo_path: Path) -> str:
    """Concatenate comment text across every source file in the repo."""

    parts: list[str] = []
    for path in _walk_source_files(repo_path):
        chunk = _extract_comments_from_file(path)
        if chunk:
            parts.append(chunk)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_self_baseline(repo_path: Path) -> SelfBaselineFinding:
    """Compare three within-repo writing surfaces and report their divergence.

    The three surfaces — commit messages (first lines, merges excluded),
    ``README.md`` at HEAD, and code comments — are each summarised by a
    :class:`StyleProfile` and projected onto a 4-dimensional normalised
    vector. We report the Euclidean distance between commit-vs-README and
    comment-vs-README, and classify the within-repo divergence as
    ``consistent`` / ``notable`` / ``significant`` based on the larger of
    the two distances:

    * both distances ``< 0.3`` -> ``consistent``
    * either in ``[0.3, 0.6)`` -> ``notable``
    * either ``>= 0.6`` -> ``significant``

    The result is a comparative-attribution signal, not a verdict on
    authorship. A non-git directory degrades gracefully — the commit-message
    surface becomes empty, which simply collapses one of the two distances.
    """

    repo_path = Path(repo_path)

    commit_text = _collect_commit_messages(repo_path)
    readme_text = _read_readme(repo_path)
    comment_text = _collect_code_comments(repo_path)

    commit_profile = metrics_for_text(commit_text, "prose")
    readme_profile = metrics_for_text(readme_text, "prose")
    comment_profile = metrics_for_text(comment_text, "prose")

    d_commit_readme = _distance(commit_profile, readme_profile)
    d_comment_readme = _distance(comment_profile, readme_profile)

    level = _classify(d_commit_readme, d_comment_readme)
    note = _build_note(d_commit_readme, d_comment_readme)

    return SelfBaselineFinding(
        commit_msg_vs_readme_distance=d_commit_readme,
        code_comment_vs_readme_distance=d_comment_readme,
        within_repo_divergence=level,
        note=note,
    )
