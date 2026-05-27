"""Meta-file density as a comparative-attribution signal. See spec §5.6.

Real long-lived projects accumulate cultural metadata over time:
``CONTRIBUTING.md`` because someone asked how to contribute, ``SECURITY.md``
because someone reported a vulnerability, ``.editorconfig`` because two
contributors disagreed about tabs. The result is a partial, organic set
of meta-files that grows by accident, not by template.

Repositories that are scaffolded in a single sitting often land the *full*
canonical set in one shot, because that is what a template or an assistant
emits when asked to "set up a proper project." This module measures how
densely populated the meta-file slot list is — a high density is the
divergent signal worth raising; a low density looks like an ordinary
working repo that has not yet grown a community.

The signal is a comparative input for human review, not a verdict on
authorship. A small repository with a perfectly complete meta-file set is
more notable than a large mature one with the same set, so we bump the
severity up a step for projects under 1000 lines of source code.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from byline.models import BoilerplateFinding

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

META_FILES_CHECKED: list[str] = [
    ".editorconfig",
    ".github/ISSUE_TEMPLATE",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/CODEOWNERS",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    ".pre-commit-config.yaml",
    ".gitattributes",
]

# Directories that should be skipped when totalling source-code lines for
# the small-project bump. Mirrors ``byline.disproportion._DEFAULT_EXCLUDE_DIRS``
# in spirit; kept local to avoid leaking the disproportion walker's broader
# responsibilities into this module.
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

# Source-code extensions for the LOC bump check.  Intentionally narrower than
# the disproportion module's full list — the spec calls out a specific set.
_LOC_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".sh",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".rb",
        ".php",
    }
)

# LOC threshold below which the severity is bumped up one step.
_SMALL_PROJECT_LOC_THRESHOLD = 1000

# Density boundaries from the spec: density < 0.4 -> normal,
# 0.4 <= density < 0.7 -> notable, density >= 0.7 -> significant.
_NOTABLE_DENSITY = 0.4
_SIGNIFICANT_DENSITY = 0.7

_Severity = Literal["normal", "notable", "significant"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield source files under ``repo_path``, skipping the usual build/cache dirs."""

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
        if candidate.suffix.lower() not in _LOC_EXTENSIONS:
            continue
        yield candidate


def _count_lines(path: Path) -> int:
    """Return the number of lines in ``path``; 0 on decoding or filesystem errors."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    except (OSError, UnicodeDecodeError):
        return 0


def _total_loc(repo_path: Path) -> int:
    """Sum line counts across every recognised source file in the repo."""

    return sum(_count_lines(p) for p in _walk_source_files(repo_path))


def _meta_file_present(repo_path: Path, entry: str) -> bool:
    """Return True if ``entry`` exists under ``repo_path``.

    Entries containing ``/`` may refer to either a file or a directory. A
    directory entry only counts as present if it contains at least one file
    (recursively); an empty directory does not.
    """

    candidate = repo_path / entry
    if "/" in entry:
        if candidate.is_dir():
            for child in candidate.rglob("*"):
                if child.is_file():
                    return True
            return False
        return candidate.is_file()
    return candidate.is_file()


def _base_severity(density: float) -> _Severity:
    """Map a density ratio to its severity bucket before the small-project bump."""

    if density < _NOTABLE_DENSITY:
        return "normal"
    if density < _SIGNIFICANT_DENSITY:
        return "notable"
    return "significant"


def _bumped(severity: _Severity) -> _Severity:
    """Move ``severity`` up one step; ``significant`` stays put (already maxed)."""

    return {
        "normal": "notable",
        "notable": "significant",
        "significant": "significant",
    }[severity]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_boilerplate(repo_path: Path) -> BoilerplateFinding:
    """Compute meta-file density as a comparative attribution signal.

    The density ratio is ``present_count / len(META_FILES_CHECKED)``. Higher
    densities indicate a repo with the full canonical set of cultural
    meta-files dropped in at once — a template-shaped pattern. Lower
    densities look like an ordinary working repo that has not yet grown
    its full community-facing metadata.

    Severity buckets (before bump):

    * ``density < 0.4`` -> ``normal``
    * ``0.4 <= density < 0.7`` -> ``notable``
    * ``density >= 0.7`` -> ``significant``

    Small projects (under 1000 lines of recognised source code) get bumped
    up one severity step, because a fully populated meta-file set is more
    notable in a tiny codebase than in a large one. The bump is applied
    once; ``significant`` does not climb beyond itself.

    Treat the result as a comparative input for human review, not a verdict.
    """

    repo_path = Path(repo_path)
    present: list[str] = [
        entry for entry in META_FILES_CHECKED if _meta_file_present(repo_path, entry)
    ]
    checked_total = len(META_FILES_CHECKED)
    density_ratio = (len(present) / checked_total) if checked_total else 0.0

    severity: _Severity = _base_severity(density_ratio)
    loc = _total_loc(repo_path)
    # A repo with literally zero source files is not "small" — the LOC
    # heuristic does not apply, so we leave the severity alone. Bumping
    # tiny-but-real codebases keeps the signal honest.
    if 0 < loc < _SMALL_PROJECT_LOC_THRESHOLD:
        severity = _bumped(severity)

    return BoilerplateFinding(
        meta_files_present=present,
        meta_files_checked=list(META_FILES_CHECKED),
        density_ratio=density_ratio,
        severity=severity,
    )
