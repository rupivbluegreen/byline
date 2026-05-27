"""Disproportion analysis between prose, code, and documentation volume. See spec §7.3.

Comparative-signal detectors that surface structural imbalances in a repository —
unusually heavy documentation relative to code, dense diagram inventories,
suspiciously regular numbered diagram filenames, and inflated comment density.
None of these is a verdict on authorship; each is a structural indicator that
diverges from typical project proportions and is worth raising for human review.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Literal

from byline.models import DisproportionFinding

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
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

_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst"})

_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cxx",
        ".rb",
        ".php",
        ".sql",
        ".lua",
        ".swift",
        ".kt",
        ".scala",
    }
)

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}
)

_DIAGRAM_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".svg", ".jpg", ".jpeg", ".gif"}
)

_BINARY_EXTENSIONS: frozenset[str] = frozenset({".bin", ".so", ".exe", ".dll", ".dylib"})

_LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "cargo.lock",
        "go.sum",
        "pnpm-lock.yaml",
        "composer.lock",
        "gemfile.lock",
    }
)

# Line-comment markers per code extension. Lines stripped of leading whitespace
# that start with one of these markers count as comment lines.
_LINE_COMMENT_MARKERS: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".sh": ("#",),
    ".bash": ("#",),
    ".zsh": ("#",),
    ".rb": ("#",),
    ".js": ("//",),
    ".ts": ("//",),
    ".tsx": ("//",),
    ".jsx": ("//",),
    ".mjs": ("//",),
    ".cjs": ("//",),
    ".go": ("//",),
    ".rs": ("//",),
    ".java": ("//",),
    ".c": ("//",),
    ".h": ("//",),
    ".cpp": ("//",),
    ".hpp": ("//",),
    ".cc": ("//",),
    ".cxx": ("//",),
    ".swift": ("//",),
    ".kt": ("//",),
    ".scala": ("//",),
    ".php": ("//", "#"),
    ".sql": ("--",),
    ".lua": ("--",),
}

# Languages with C-style /* ... */ block comments.
_BLOCK_COMMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cxx",
        ".swift",
        ".kt",
        ".scala",
        ".php",
        ".sql",
        ".css",
    }
)

_NUMBERED_DIAGRAM_RE = re.compile(
    r"diagram-(\d{1,3})[-_].+\.(png|svg|jpg|jpeg|gif)$", re.IGNORECASE
)

_DIAGRAM_KEYWORD_RE = re.compile(r"(diagram|architecture)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _walk_repo(
    repo_path: Path, exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS
) -> Iterator[Path]:
    """Yield every non-excluded file under ``repo_path``.

    Skips any path whose ancestry contains a directory whose name is in
    ``exclude_dirs``. Returns nothing if ``repo_path`` does not exist or is
    not a directory.
    """

    repo_path = Path(repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return

    for candidate in repo_path.rglob("*"):
        try:
            rel_parts = candidate.relative_to(repo_path).parts
        except ValueError:
            continue
        if any(part in exclude_dirs for part in rel_parts[:-1]):
            continue
        if not candidate.is_file():
            continue
        yield candidate


def _is_doc(path: Path) -> bool:
    """True for Markdown or reStructuredText documentation files."""

    return path.suffix.lower() in _DOC_EXTENSIONS


def _is_code(path: Path) -> bool:
    """True for recognised source-code file extensions, excluding lockfiles and binaries."""

    name_lower = path.name.lower()
    if name_lower in _LOCKFILE_NAMES:
        return False
    suffix = path.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return False
    return suffix in _CODE_EXTENSIONS


def _is_image(path: Path) -> bool:
    """True for image extensions used to surface diagram/architecture assets."""

    return path.suffix.lower() in _IMAGE_EXTENSIONS


def _count_lines(path: Path) -> int:
    """Return the number of lines in ``path``; 0 on decoding or filesystem errors."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    except (OSError, UnicodeDecodeError):
        return 0


def _total_code_lines(repo_path: Path) -> int:
    """Sum line counts across every source-code file in the repo (skip lists honoured)."""

    return sum(_count_lines(p) for p in _walk_repo(repo_path) if _is_code(p))


def _count_comment_lines_in_file(path: Path) -> tuple[int, int]:
    """Return ``(comment_lines, total_lines)`` for a single source file.

    A line is a comment line if (a) after stripping leading whitespace it
    starts with one of the language's line-comment markers, or (b) it falls
    inside an open ``/* ... */`` block (for C-style languages). Returns
    ``(0, 0)`` on decode or filesystem errors.
    """

    suffix = path.suffix.lower()
    markers = _LINE_COMMENT_MARKERS.get(suffix, ())
    supports_block = suffix in _BLOCK_COMMENT_EXTENSIONS

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, 0

    lines = text.splitlines()
    if not lines:
        return 0, 0

    comment_lines = 0
    in_block = False
    for raw in lines:
        stripped = raw.strip()
        if in_block:
            comment_lines += 1
            if "*/" in stripped:
                in_block = False
            continue
        if supports_block and stripped.startswith("/*"):
            comment_lines += 1
            if "*/" not in stripped[2:]:
                in_block = True
            continue
        if markers and any(stripped.startswith(marker) for marker in markers):
            comment_lines += 1

    return comment_lines, len(lines)


def _severity_from_thresholds(
    observed: float, significant: float, notable: float
) -> Literal["info", "notable", "significant"]:
    """Map an observed value to a severity bucket via the spec thresholds."""

    if observed > significant:
        return "significant"
    if observed > notable:
        return "notable"
    return "info"


def _severity_gte(
    observed: float, significant: float, notable: float
) -> Literal["info", "notable", "significant"]:
    """Same as :func:`_severity_from_thresholds` but using ``>=`` boundaries.

    Used for count-style signals where the threshold values are inclusive
    (e.g. "6 or more diagrams is significant").
    """

    if observed >= significant:
        return "significant"
    if observed >= notable:
        return "notable"
    return "info"


# ---------------------------------------------------------------------------
# Public detectors
# ---------------------------------------------------------------------------


def doc_to_code_ratio(repo_path: Path) -> DisproportionFinding:
    """Compare prose-documentation volume to source-code volume.

    Sums lines across ``*.md`` and ``*.rst`` files and divides by the line
    total of recognised source-code files. Lockfiles, vendored trees, build
    artefacts, virtualenvs and binary blobs are excluded. A ratio above 1.0
    is flagged as ``significant``, above 0.5 as ``notable``, otherwise
    ``info``. The reference threshold reported on the finding is 0.5 — the
    notable boundary.
    """

    repo_path = Path(repo_path)
    doc_lines = 0
    code_lines = 0
    for path in _walk_repo(repo_path):
        if _is_doc(path):
            doc_lines += _count_lines(path)
        elif _is_code(path):
            code_lines += _count_lines(path)

    observed = (doc_lines / code_lines) if code_lines > 0 else 0.0
    severity = _severity_from_thresholds(observed, significant=1.0, notable=0.5)
    description = (
        f"Documentation-to-code ratio of {observed:.2f} "
        f"({doc_lines} doc lines / {code_lines} code lines) is a structural signal "
        "of how prose volume compares to working code; treat as a comparative "
        "indicator, not a verdict."
    )
    return DisproportionFinding(
        name="doc_to_code_ratio",
        observed=observed,
        threshold=0.5,
        severity=severity,
        description=description,
    )


def diagram_count(repo_path: Path) -> DisproportionFinding:
    """Count diagram-style images and weigh them against project size.

    An image counts toward the total if it lives directly under ``docs/`` (or
    any descendant), or if any segment of its path matches ``*diagram*`` or
    ``*architecture*`` (case-insensitive). For repos under 2000 lines of
    code, ``>= 6`` images is ``significant`` and ``>= 4`` is ``notable``;
    above 2000 LOC the same boundaries are scaled by image density per
    1000 LOC (>= 3 = significant, >= 2 = notable).
    """

    repo_path = Path(repo_path)
    diagram_paths: list[Path] = []
    for path in _walk_repo(repo_path):
        if path.suffix.lower() not in _DIAGRAM_IMAGE_EXTENSIONS:
            continue
        try:
            rel_parts = path.relative_to(repo_path).parts
        except ValueError:
            continue
        in_docs = any(part.lower() == "docs" for part in rel_parts)
        keyword_match = any(_DIAGRAM_KEYWORD_RE.search(part) for part in rel_parts)
        if in_docs or keyword_match:
            diagram_paths.append(path)

    count = len(diagram_paths)
    loc = _total_code_lines(repo_path)

    if loc < 2000:
        severity = _severity_gte(count, significant=6, notable=4)
        density_note = f"in a {loc}-LOC project"
    else:
        density = count / max(loc, 1) * 1000
        severity = _severity_gte(density, significant=3, notable=2)
        density_note = f"density {density:.2f} diagrams per 1000 LOC over {loc} LOC"

    description = (
        f"{count} diagram/architecture image(s) found {density_note}; a heavy "
        "diagram inventory relative to code is a comparative-attribution signal, "
        "not a verdict."
    )
    return DisproportionFinding(
        name="diagram_count",
        observed=float(count),
        threshold=4.0,
        severity=severity,
        description=description,
    )


def numbered_diagram_pattern(repo_path: Path) -> DisproportionFinding:
    """Detect unusually regular numbered diagram filenames (``diagram-01-foo.png`` …).

    Files matching ``diagram-\\d{1,3}[-_].+\\.(png|svg|jpg|jpeg|gif)`` anywhere in
    the tree contribute their numeric prefix. The reported observation is the
    length of the longest consecutive run found, considering both runs that
    start at 1 and any run elsewhere in the sequence. ``>= 5`` is
    ``significant``, ``>= 3`` is ``notable``, otherwise ``info``.
    """

    repo_path = Path(repo_path)
    numbers: set[int] = set()
    for path in _walk_repo(repo_path):
        m = _NUMBERED_DIAGRAM_RE.search(path.name)
        if m:
            numbers.add(int(m.group(1)))

    longest_run = _longest_consecutive_run(numbers)
    severity = _severity_gte(longest_run, significant=5, notable=3)
    description = (
        f"Longest consecutive run of numbered diagram filenames: {longest_run} "
        "(e.g. diagram-01-..., diagram-02-...); a tightly sequenced diagram set "
        "is a structural signal, not a verdict."
    )
    return DisproportionFinding(
        name="numbered_diagram_pattern",
        observed=float(longest_run),
        threshold=3.0,
        severity=severity,
        description=description,
    )


def _longest_consecutive_run(numbers: set[int]) -> int:
    """Return the length of the longest consecutive integer run in ``numbers``."""

    if not numbers:
        return 0
    longest = 0
    for n in numbers:
        # Only start counting from the lower end of a run.
        if (n - 1) in numbers:
            continue
        length = 1
        while (n + length) in numbers:
            length += 1
        if length > longest:
            longest = length
    return longest


def comment_density(repo_path: Path) -> DisproportionFinding:
    """Aggregate comment-to-total-line ratio across all source files.

    Counts line comments (``#``, ``//``, ``--``) and lines within C-style
    ``/* ... */`` blocks. Returns the overall ratio of comment lines to
    total source lines. ``> 0.4`` is ``significant``, ``> 0.25`` is
    ``notable``, else ``info``.
    """

    repo_path = Path(repo_path)
    total_comments = 0
    total_lines = 0
    for path in _walk_repo(repo_path):
        if not _is_code(path):
            continue
        comments, lines = _count_comment_lines_in_file(path)
        total_comments += comments
        total_lines += lines

    observed = (total_comments / total_lines) if total_lines > 0 else 0.0
    severity = _severity_from_thresholds(observed, significant=0.4, notable=0.25)
    description = (
        f"Overall comment density of {observed:.2f} "
        f"({total_comments} comment lines / {total_lines} source lines) is a "
        "structural signal of explanatory-prose volume in code; treat as a "
        "comparative indicator, not a verdict."
    )
    return DisproportionFinding(
        name="comment_density",
        observed=observed,
        threshold=0.25,
        severity=severity,
        description=description,
    )


def analyze(repo_path: Path) -> list[DisproportionFinding]:
    """Run every disproportion detector and return the combined findings list."""

    return [
        doc_to_code_ratio(repo_path),
        diagram_count(repo_path),
        numbered_diagram_pattern(repo_path),
        comment_density(repo_path),
    ]
