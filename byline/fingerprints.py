"""Pattern-based scanning for catalogued stylistic signals in prose and shell code.

Implements the fingerprint detector defined in spec §7.2. The "fingerprints"
located here are stylistic signals and patterns of interest — they are
comparative indicators, not proof of authorship. Three entry points scan a
single Markdown document (:func:`scan_readme`), a single shell-style script
(:func:`scan_shell_script`), or every relevant file in a directory tree
(:func:`scan_repo`). Each returns a list of :class:`~byline.models.FingerprintHit`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from importlib import resources
from pathlib import Path

from byline.metrics import _is_emoji, em_dash_density
from byline.models import FingerprintHit

# ---------------------------------------------------------------------------
# Module-level lazy caches
# ---------------------------------------------------------------------------

_PHRASES: list[dict] | None = None
_HEADERS: list[dict] | None = None
_COMPILED_PHRASES: list[tuple[dict, re.Pattern[str]]] | None = None
_COMPILED_HEADER_PATTERNS: list[tuple[dict, re.Pattern[str]]] | None = None

# Cap on excerpt context width — keep hits compact.
_EXCERPT_MAX = 200

# File-size cap for repo scanning (500KB).
_MAX_FILE_SIZE = 500 * 1024

# Directory names skipped wholesale during repo scanning.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "venv", "node_modules", "vendor", "__pycache__", ".tox", ".mypy_cache"}
)

_HEADER_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _read_data_file(name: str) -> str:
    """Read a JSON resource from ``byline.data``, falling back to filesystem."""

    try:
        return resources.files("byline.data").joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):  # pragma: no cover
        return (Path(__file__).parent / "data" / name).read_text(encoding="utf-8")


def _load_phrases() -> list[dict]:
    """Lazily load and cache the catalogued AI-associated phrase list."""

    global _PHRASES
    if _PHRASES is not None:
        return _PHRASES
    raw = _read_data_file("ai_phrases.json")
    _PHRASES = json.loads(raw)
    return _PHRASES


def _load_headers() -> list[dict]:
    """Lazily load and cache the catalogued AI-associated header list."""

    global _HEADERS
    if _HEADERS is not None:
        return _HEADERS
    raw = _read_data_file("ai_section_headers.json")
    _HEADERS = json.loads(raw)
    return _HEADERS


def _compiled_phrases() -> list[tuple[dict, re.Pattern[str]]]:
    """Compile each phrase entry to a case-insensitive regex, cached."""

    global _COMPILED_PHRASES
    if _COMPILED_PHRASES is not None:
        return _COMPILED_PHRASES
    compiled: list[tuple[dict, re.Pattern[str]]] = []
    for entry in _load_phrases():
        pattern = entry["pattern"]
        if entry["type"] == "literal":
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        else:
            regex = re.compile(pattern, re.IGNORECASE)
        compiled.append((entry, regex))
    _COMPILED_PHRASES = compiled
    return _COMPILED_PHRASES


def _compiled_header_patterns() -> list[tuple[dict, re.Pattern[str]]]:
    """Compile each header entry to a case-insensitive literal-anchored regex, cached."""

    global _COMPILED_HEADER_PATTERNS
    if _COMPILED_HEADER_PATTERNS is not None:
        return _COMPILED_HEADER_PATTERNS
    compiled: list[tuple[dict, re.Pattern[str]]] = []
    for entry in _load_headers():
        pattern = entry["pattern"]
        if entry["type"] == "literal":
            regex = re.compile(r"^\s*" + re.escape(pattern) + r"\s*$", re.IGNORECASE)
        else:
            regex = re.compile(pattern, re.IGNORECASE)
        compiled.append((entry, regex))
    _COMPILED_HEADER_PATTERNS = compiled
    return _COMPILED_HEADER_PATTERNS


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _line_of_offset(text: str, offset: int) -> int:
    """Return the 1-indexed line number containing byte offset ``offset``."""

    # count("\n", 0, offset) gives the number of newlines strictly before offset.
    return text.count("\n", 0, offset) + 1


def _excerpt_around(text: str, start: int, end: int) -> str:
    """Return a single-line excerpt of <= ``_EXCERPT_MAX`` chars around a match."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end_nl = text.find("\n", end)
    line_end = len(text) if line_end_nl == -1 else line_end_nl
    line = text[line_start:line_end].strip()
    if len(line) > _EXCERPT_MAX:
        # Centre the excerpt around the match.
        rel = max(0, start - line_start - _EXCERPT_MAX // 2)
        line = line[rel : rel + _EXCERPT_MAX]
    return line


def _iter_headers(text: str) -> Iterable[tuple[int, str, str]]:
    """Yield ``(1-indexed line number, raw line, captured header text)`` for ATX headers."""

    for idx, raw in enumerate(text.splitlines(), start=1):
        m = _HEADER_RE.match(raw)
        if m:
            yield idx, raw, m.group(1).strip()


# ---------------------------------------------------------------------------
# scan_readme
# ---------------------------------------------------------------------------


def scan_readme(text: str, file_path: str) -> list[FingerprintHit]:
    """Scan Markdown prose for catalogued stylistic signals.

    Returns one hit per phrase occurrence, plus at most one hit each for the
    AI-section-header cluster, emoji-header cluster, and em-dash-overdose
    structural signals. ``file_path`` is recorded verbatim on every hit.
    """

    hits: list[FingerprintHit] = []

    # 1. Phrase hits — one per occurrence.
    for entry, regex in _compiled_phrases():
        for m in regex.finditer(text):
            start, end = m.start(), m.end()
            hits.append(
                FingerprintHit(
                    file_path=file_path,
                    line_number=_line_of_offset(text, start),
                    pattern=entry["pattern"],
                    category="phrase",
                    excerpt=_excerpt_around(text, start, end),
                )
            )

    # 2. AI section header cluster — single hit when >= 6 catalogued headers found.
    matched_headers: list[str] = []
    header_lines = list(_iter_headers(text))
    for _, _, captured in header_lines:
        for _entry, regex in _compiled_header_patterns():
            if regex.match(captured):
                matched_headers.append(captured)
                break
    if len(matched_headers) >= 6:
        listing = ", ".join(matched_headers)
        excerpt = f"Catalogued AI-associated headers found: {listing}"
        if len(excerpt) > _EXCERPT_MAX:
            excerpt = excerpt[:_EXCERPT_MAX]
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=None,
                pattern="ai_section_header_cluster",
                category="ai_section_header",
                excerpt=excerpt,
            )
        )

    # 3. Emoji-header cluster — >= 4 headers whose text starts with an emoji.
    emoji_headers = [
        (line_no, captured)
        for line_no, _raw, captured in header_lines
        if captured and _is_emoji(captured[0])
    ]
    if len(emoji_headers) >= 4:
        listing = "; ".join(f"L{ln}: {cap}" for ln, cap in emoji_headers[:6])
        excerpt = f"{len(emoji_headers)} emoji-prefixed headers — {listing}"
        if len(excerpt) > _EXCERPT_MAX:
            excerpt = excerpt[:_EXCERPT_MAX]
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=None,
                pattern="emoji_header_cluster",
                category="structure",
                excerpt=excerpt,
            )
        )

    # 4. Em-dash overdose — density > 8.0 per 1000 words.
    density = em_dash_density(text)
    if density > 8.0:
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=None,
                pattern="em_dash_overdose",
                category="structure",
                excerpt=f"em-dash density {density:.2f} per 1000 words (threshold 8.0)",
            )
        )

    return hits


# ---------------------------------------------------------------------------
# scan_shell_script
# ---------------------------------------------------------------------------

_SHELL_BANNER_RE = re.compile(r"^\s*#?\s*[=\-#]{5,}\s*$")
_PROGRESS_BRACKET_RE = re.compile(r"\[\d+/\d+\]")
_WHAT_IT_DOES_RE = re.compile(r"(WHAT IT DOES:|WHEN TO RUN:)", re.IGNORECASE)
_PRINTF_CLOSE_RE = re.compile(
    r"(Setup complete!|Cleanup complete!|Installation complete!)", re.IGNORECASE
)


def scan_shell_script(text: str, file_path: str) -> list[FingerprintHit]:
    """Scan a shell-style script (or Dockerfile) for catalogued stylistic signals.

    Detects banner comments, ``[N/M]`` progress markers, ``WHAT IT DOES:`` /
    ``WHEN TO RUN:`` block headers, and ``Setup complete!`` / ``Cleanup complete!``
    / ``Installation complete!`` printf-banner closers.
    """

    hits: list[FingerprintHit] = []

    # 1. Banner comments — line-anchored.
    for idx, raw in enumerate(text.splitlines(), start=1):
        if _SHELL_BANNER_RE.match(raw):
            excerpt = raw.strip()
            if len(excerpt) > _EXCERPT_MAX:
                excerpt = excerpt[:_EXCERPT_MAX]
            hits.append(
                FingerprintHit(
                    file_path=file_path,
                    line_number=idx,
                    pattern="banner_comment",
                    category="shell_banner",
                    excerpt=excerpt,
                )
            )

    # 2. [N/M] progress markers — one hit per occurrence.
    for m in _PROGRESS_BRACKET_RE.finditer(text):
        start, end = m.start(), m.end()
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=_line_of_offset(text, start),
                pattern="progress_marker",
                category="progress_ux",
                excerpt=_excerpt_around(text, start, end),
            )
        )

    # 3. WHAT IT DOES: / WHEN TO RUN: blocks.
    for m in _WHAT_IT_DOES_RE.finditer(text):
        start, end = m.start(), m.end()
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=_line_of_offset(text, start),
                pattern="what_it_does_block",
                category="structure",
                excerpt=_excerpt_around(text, start, end),
            )
        )

    # 4. printf-banner closing blocks.
    for m in _PRINTF_CLOSE_RE.finditer(text):
        start, end = m.start(), m.end()
        hits.append(
            FingerprintHit(
                file_path=file_path,
                line_number=_line_of_offset(text, start),
                pattern="printf_banner_close",
                category="shell_banner",
                excerpt=_excerpt_around(text, start, end),
            )
        )

    return hits


# ---------------------------------------------------------------------------
# scan_repo
# ---------------------------------------------------------------------------


def _is_dockerfile(path: Path) -> bool:
    """True for files whose basename is some case-form of ``Dockerfile``."""

    return path.is_file() and path.name.lower() == "dockerfile"


def _should_skip_dir(name: str) -> bool:
    """True for directory names that should be pruned from the walk."""

    return name in _SKIP_DIRS or name.startswith(".") and name not in {".github"}


def _read_file_safely(path: Path) -> str | None:
    """Return file text, or ``None`` if too large or undecodable."""

    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > _MAX_FILE_SIZE:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def scan_repo(repo_path: Path) -> list[FingerprintHit]:
    """Walk ``repo_path`` and scan each relevant file with the right detector.

    Returns the combined list of hits across the tree. Paths on each hit are
    recorded relative to ``repo_path``. Hidden, virtualenv, ``node_modules``,
    ``vendor`` and ``__pycache__`` directories are pruned. Files larger than
    500KB are skipped. Returns an empty list if ``repo_path`` does not exist
    or is not a directory.
    """

    repo_path = Path(repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return []

    hits: list[FingerprintHit] = []

    for candidate in sorted(repo_path.rglob("*")):
        # Skip anything whose ancestry includes a pruned directory.
        try:
            rel_parts = candidate.relative_to(repo_path).parts
        except ValueError:
            continue
        if any(_should_skip_dir(part) for part in rel_parts[:-1]):
            continue
        if not candidate.is_file():
            continue

        rel_path = candidate.relative_to(repo_path)
        rel_str = str(rel_path)
        suffix = candidate.suffix.lower()
        name_lower = candidate.name.lower()

        content: str | None = None

        if suffix == ".md":
            content = _read_file_safely(candidate)
            if content is not None:
                hits.extend(scan_readme(content, rel_str))
        elif suffix == ".sh":
            content = _read_file_safely(candidate)
            if content is not None:
                hits.extend(scan_shell_script(content, rel_str))
        elif name_lower == "dockerfile":
            content = _read_file_safely(candidate)
            if content is not None:
                hits.extend(scan_shell_script(content, rel_str))
        elif suffix in (".yml", ".yaml"):
            content = _read_file_safely(candidate)
            if content is not None:
                hits.extend(scan_readme(content, rel_str))

    return hits
