"""Style and readability signal computations over prose and code.

Implements the comparative stylistic signals defined in spec §7.1. Each public
function is a pure mapping from text to a float signal. Aggregator
``metrics_for_text`` bundles signals into a :class:`StyleProfile` for either
prose or shell-script input, supporting downstream comparative-divergence
analysis between a target and a baseline.
"""

from __future__ import annotations

import logging
import re
import string
from importlib import resources
from typing import Literal

import textstat

from byline.models import StyleProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy caches
# ---------------------------------------------------------------------------

_LANGUAGE_TOOL: object | None = None
_LANGUAGE_TOOL_INIT_FAILED: bool = False
_COMMON_WORDS: set[str] | None = None


# ---------------------------------------------------------------------------
# Emoji range helper
# ---------------------------------------------------------------------------

# Inclusive Unicode ranges that cover the common emoji code points used in
# Markdown headers. We intentionally include surrounding symbol blocks
# (Misc Symbols, Dingbats, Misc Technical) so glyphs like ✨, ⚙, ⌚ are
# recognised as emoji-style signals.
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),  # Misc Symbols and Pictographs
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F680, 0x1F6FF),  # Transport and Map
    (0x1F700, 0x1F77F),  # Alchemical
    (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
    (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
    (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
    (0x1FA00, 0x1FA6F),  # Symbols and Pictographs Extended-A
    (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-B
    (0x2600, 0x26FF),    # Misc Symbols (☀, ⚙, ...)
    (0x2700, 0x27BF),    # Dingbats (✨, ✅, ...)
    (0x2300, 0x23FF),    # Misc Technical (⌚, ⏱, ...)
    (0x2B00, 0x2BFF),    # Misc Symbols and Arrows
)


def _is_emoji(ch: str) -> bool:
    """Return True if ``ch`` is a single character in any tracked emoji range.

    Intentionally permissive — variation selectors and ZWJ sequences are not
    decomposed; the leading base character is what we check.
    """

    if not ch:
        return False
    cp = ord(ch[0])
    for lo, hi in _EMOJI_RANGES:
        if lo <= cp <= hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Word tokenisation helpers
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    """Whitespace-split token count — the spec's definition of ``word``.

    Tokens that are pure punctuation (e.g. a bare ``—`` or ``--`` between
    surrounding spaces) are excluded so that the em-dash signal expresses
    "dashes per N actual words", not "dashes per N tokens including the
    dashes themselves".
    """

    tokens = text.split()
    return sum(1 for tok in tokens if any(ch.isalnum() for ch in tok))


_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip ``string.punctuation``, split on whitespace, drop empties."""

    cleaned = text.lower().translate(_PUNCT_TABLE)
    return [tok for tok in cleaned.split() if tok]


# ---------------------------------------------------------------------------
# em_dash_density
# ---------------------------------------------------------------------------


def em_dash_density(text: str) -> float:
    """Em-dash signal density per 1000 whitespace-split words.

    Counts U+2014 (``—``) and the two-hyphen ``--`` sequence (a common
    typographic stand-in for the em-dash). Returns ``0.0`` for empty input.
    """

    words = _word_count(text)
    if words == 0:
        return 0.0
    count = text.count("—") + text.count("--")
    return (count / words) * 1000


# ---------------------------------------------------------------------------
# emoji_in_headers_ratio
# ---------------------------------------------------------------------------

_ATX_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$")


def emoji_in_headers_ratio(markdown: str) -> float:
    """Fraction of Markdown ATX headers whose first non-whitespace char is emoji.

    Returns ``0.0`` when no ATX headers are present.
    """

    total = 0
    matching = 0
    for raw_line in markdown.splitlines():
        line = raw_line.lstrip()
        m = _ATX_HEADER_RE.match(line)
        if not m:
            continue
        total += 1
        captured = m.group(1).lstrip()
        if captured and _is_emoji(captured[0]):
            matching += 1
    if total == 0:
        return 0.0
    return matching / total


# ---------------------------------------------------------------------------
# avg_sentence_length
# ---------------------------------------------------------------------------


def avg_sentence_length(text: str) -> float:
    """Mean sentence length signal via ``textstat.avg_sentence_length``.

    Returns ``0.0`` when input is empty or textstat returns a falsy value.
    """

    if not text:
        return 0.0
    try:
        # ``words_per_sentence`` is the modern textstat alias; fall back to the
        # historical ``avg_sentence_length`` name for older releases.
        fn = getattr(textstat, "words_per_sentence", None) or textstat.avg_sentence_length
        value = fn(text)
    except Exception:  # pragma: no cover — textstat is generally robust
        return 0.0
    if not value:
        return 0.0
    return float(value)


# ---------------------------------------------------------------------------
# type_token_ratio
# ---------------------------------------------------------------------------


def type_token_ratio(text: str) -> float:
    """Lexical diversity signal: unique tokens / total tokens.

    Tokens are lowercased, stripped of ``string.punctuation``, then split on
    whitespace. Returns ``0.0`` when there are no tokens.
    """

    tokens = _normalize_tokens(text)
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)


# ---------------------------------------------------------------------------
# typo_rate (language_tool_python — lazy init)
# ---------------------------------------------------------------------------


def _get_language_tool() -> object | None:
    """Lazily construct and cache the LanguageTool singleton.

    Returns ``None`` if initialisation fails (e.g. JVM or network unavailable).
    Importing this module must NOT trigger the LanguageTool download — the
    instance is only created on first call.
    """

    global _LANGUAGE_TOOL, _LANGUAGE_TOOL_INIT_FAILED
    if _LANGUAGE_TOOL is not None:
        return _LANGUAGE_TOOL
    if _LANGUAGE_TOOL_INIT_FAILED:
        return None
    try:
        import language_tool_python  # type: ignore[import-not-found]

        _LANGUAGE_TOOL = language_tool_python.LanguageTool("en-US")
    except (LookupError, OSError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "LanguageTool unavailable — typo_rate signal will return 0.0: %s",
            exc,
        )
        _LANGUAGE_TOOL_INIT_FAILED = True
        _LANGUAGE_TOOL = None
        return None
    return _LANGUAGE_TOOL


def typo_rate(text: str) -> float:
    """Typo/grammar-error signal per 1000 whitespace-split words.

    Uses ``language_tool_python`` lazily; if the tool cannot be initialised
    (JVM missing, no network for the download, etc.) the signal degrades
    gracefully to ``0.0`` with a logged warning.
    """

    words = _word_count(text)
    if words == 0:
        return 0.0
    tool = _get_language_tool()
    if tool is None:
        return 0.0
    try:
        matches = tool.check(text)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LanguageTool check failed — returning 0.0: %s", exc)
        return 0.0
    return (len(matches) / words) * 1000


# ---------------------------------------------------------------------------
# sophistication_score
# ---------------------------------------------------------------------------


def _load_common_words() -> set[str]:
    """Lazily load and cache the top-5000 common-words reference set."""

    global _COMMON_WORDS
    if _COMMON_WORDS is not None:
        return _COMMON_WORDS
    try:
        raw = resources.files("byline.data").joinpath("common_5000.txt").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):  # pragma: no cover
        from pathlib import Path

        raw = (Path(__file__).parent / "data" / "common_5000.txt").read_text(encoding="utf-8")
    _COMMON_WORDS = {line.strip().lower() for line in raw.splitlines() if line.strip()}
    return _COMMON_WORDS


def sophistication_score(text: str) -> float:
    """Fraction of tokens not present in the top-5000 common-words reference set.

    The spec phrases this as a "percentage"; we return it as a 0.0–1.0 fraction
    (the standard interpretation), where higher means more uncommon vocabulary.
    Returns ``0.0`` when there are no tokens.
    """

    tokens = _normalize_tokens(text)
    if not tokens:
        return 0.0
    common = _load_common_words()
    uncommon = sum(1 for tok in tokens if tok not in common)
    return uncommon / len(tokens)


# ---------------------------------------------------------------------------
# banner_comment_density
# ---------------------------------------------------------------------------

# Match optional leading "#" + whitespace, then 5+ separator chars (= - #).
_BANNER_RE = re.compile(r"^\s*#?\s*[=\-#]{5,}\s*$")


def banner_comment_density(script_text: str) -> float:
    """Banner-comment signal: lines like ``# =====`` per 100 script lines.

    Returns ``0.0`` when the script is empty.
    """

    lines = script_text.splitlines()
    total = len(lines)
    if total == 0:
        return 0.0
    count = sum(1 for line in lines if _BANNER_RE.match(line))
    return (count / total) * 100


# ---------------------------------------------------------------------------
# progress_ux_score
# ---------------------------------------------------------------------------

_PROGRESS_BRACKET_RE = re.compile(r"\[\d+/\d+\]")
_PROGRESS_SUBSTRINGS: tuple[str, ...] = (
    "expected output:",
    "next steps:",
    "what this does:",
    "when to run:",
    "setup complete!",
    "cleanup complete!",
)


def progress_ux_score(text: str) -> float:
    """Heuristic UX-scaffolding signal in 0.0–1.0.

    Counts distinct signal markers — ``[N/M]`` progress brackets and the
    case-insensitive substrings ``Expected output:``, ``Next steps:``,
    ``What this does:``, ``When to run:``, ``Setup complete!``,
    ``Cleanup complete!`` — and returns ``min(1.0, signals_present / 4.0)``
    so that any four distinct signals fully saturate the score.
    """

    if not text:
        return 0.0
    lower = text.lower()
    signals = 0
    if _PROGRESS_BRACKET_RE.search(text):
        signals += 1
    for needle in _PROGRESS_SUBSTRINGS:
        if needle in lower:
            signals += 1
    return min(1.0, signals / 4.0)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def metrics_for_text(text: str, kind: Literal["prose", "shell"]) -> StyleProfile:
    """Bundle stylistic signals for ``text`` into a :class:`StyleProfile`.

    For ``kind="prose"`` the six prose-oriented signals are computed and the
    two shell-only signals are zeroed. For ``kind="shell"`` only the
    shell-script signals (banner-comment and progress-UX) are populated.
    """

    if kind == "prose":
        return StyleProfile(
            em_dash_density=em_dash_density(text),
            emoji_in_headers_ratio=emoji_in_headers_ratio(text),
            avg_sentence_length=avg_sentence_length(text),
            type_token_ratio=type_token_ratio(text),
            typo_rate=typo_rate(text),
            sophistication_score=sophistication_score(text),
            banner_comment_density=0.0,
            progress_ux_score=0.0,
        )
    if kind == "shell":
        return StyleProfile(
            em_dash_density=0.0,
            emoji_in_headers_ratio=0.0,
            avg_sentence_length=0.0,
            type_token_ratio=0.0,
            typo_rate=0.0,
            sophistication_score=0.0,
            banner_comment_density=banner_comment_density(text),
            progress_ux_score=progress_ux_score(text),
        )
    raise ValueError(f"Unknown kind: {kind!r} (expected 'prose' or 'shell')")
