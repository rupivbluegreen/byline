"""First-person voice presence and AI-use disclosure signals. See spec §5.5.

Two comparative signals about how the author talks about their own work:

* First-person voice in the README — a positive presence signal. Authors who
  describe a project in their own voice ("I built this because...") tend to
  produce prose that is identifiably theirs. We measure density per 1000 words
  so short and long READMEs are comparable.

* Explicit AI-use disclosure — a positive trust signal. When a repository
  acknowledges where AI tools contributed, that is information a reviewer
  benefits from. We surface the matched sentence so it can be quoted in
  a report rather than hidden.

Neither signal is a verdict; both are comparative inputs for human review.
"""

from __future__ import annotations

import re
from pathlib import Path

from byline.models import VoiceFinding

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to strip fenced code blocks before counting first-person pronouns,
# so that example snippets that happen to use "I" do not inflate the count.
_FENCED_CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)

# Case-sensitive first-person patterns: standalone capital "I" and its
# common contracted forms. The capital I matters — lowercase "i" is almost
# always a loop variable, not a pronoun.
_CAPITAL_I_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Standalone "I" — explicitly disallow a following apostrophe so we do
    # not double-count the same "I" once as a standalone pronoun and once
    # as part of a contraction (I'm, I've, ...).
    re.compile(r"\bI\b(?!')"),
    re.compile(r"\bI'm\b"),
    re.compile(r"\bI've\b"),
    re.compile(r"\bI'd\b"),
    re.compile(r"\bI'll\b"),
)

# Case-insensitive first-person possessives and object pronouns.
_LOWER_PRONOUN_RE = re.compile(r"\b(?:my|mine|me)\b", re.IGNORECASE)

# Files that conventionally carry AI-use disclosure. Matched case-insensitively
# against directory entries so that, e.g., "Readme.md" still works.
_DISCLOSURE_FILES: tuple[str, ...] = (
    "README.md",
    "AI_USE.md",
    "ATTRIBUTION.md",
    "AUTHORS.md",
    "DISCLOSURE.md",
)

# Mentions of a specific AI tool or model. The right-hand sides are loose
# on purpose ("ai-assist", "ai generated", "ai help") to cover common phrasings.
_TOOL_MENTION_RE = re.compile(
    r"\b(claude|chatgpt|gpt[- ]?\d|copilot|cursor|llm|ai[- ]?(?:assist|generated|help))",
    re.IGNORECASE,
)

# Verbs that, when paired with a tool mention nearby, indicate acknowledgement
# of use rather than an incidental mention.
_USE_VERB_RE = re.compile(
    r"\b(used|with help of|assisted|generated|wrote|drafted)",
    re.IGNORECASE,
)

# Maximum distance (in characters) between the tool mention and the use verb
# for the pair to count as a disclosure. Tight enough to require co-occurrence
# in the same sentence or clause; loose enough to tolerate intervening words.
_DISCLOSURE_PROXIMITY_CHARS = 50

# README density threshold above which we mark the README as having a clear
# first-person voice. The unit is occurrences per 1000 words.
_FIRST_PERSON_DENSITY_THRESHOLD = 2.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_fenced_code(text: str) -> str:
    """Remove fenced code blocks so example snippets do not skew prose counts."""
    return _FENCED_CODE_RE.sub(" ", text)


def _find_disclosure_file(repo_path: Path, target: str) -> Path | None:
    """Return the first directory entry whose filename matches ``target`` case-insensitively."""
    if not repo_path.is_dir():
        return None
    target_lower = target.lower()
    for entry in repo_path.iterdir():
        if entry.is_file() and entry.name.lower() == target_lower:
            return entry
    return None


def _extract_sentence(text: str, anchor_start: int, anchor_end: int) -> str:
    """Return the sentence containing the span [anchor_start, anchor_end], trimmed to 200 chars.

    Sentence boundaries are ``.``, ``!``, ``?``, or a blank-line paragraph break.
    """
    # Find the nearest sentence-end boundary before the anchor.
    left = 0
    for match in re.finditer(r"[.!?]\s+|\n\s*\n", text[:anchor_start]):
        left = match.end()
    # Find the nearest sentence-end boundary at or after the anchor.
    right_match = re.search(r"[.!?](?=\s|$)|\n\s*\n", text[anchor_end:])
    if right_match is None:
        right = len(text)
    else:
        # Include the punctuation itself if present.
        right = anchor_end + right_match.end()
    sentence = text[left:right].strip()
    if len(sentence) > 200:
        sentence = sentence[:200].rstrip()
    return sentence


def _scan_for_disclosure(content: str) -> tuple[int, int] | None:
    """Return the (start, end) span of a tool/verb pair within 50 chars, or None."""
    tool_hits = list(_TOOL_MENTION_RE.finditer(content))
    if not tool_hits:
        return None
    verb_hits = list(_USE_VERB_RE.finditer(content))
    if not verb_hits:
        return None
    for tool in tool_hits:
        for verb in verb_hits:
            # Distance is between the closest edges of the two matches.
            if tool.start() <= verb.start():
                gap = verb.start() - tool.end()
            else:
                gap = tool.start() - verb.end()
            if gap < 0:
                # Overlapping (rare) — treat as co-occurring.
                gap = 0
            if gap <= _DISCLOSURE_PROXIMITY_CHARS:
                start = min(tool.start(), verb.start())
                end = max(tool.end(), verb.end())
                return start, end
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def count_first_person(text: str) -> int:
    """Count whole-word occurrences of first-person pronouns.

    Case-sensitive for ``I``; case-insensitive for ``my``, ``mine``, ``me``,
    ``I'm``, ``I've``, ``I'd``. Skip content inside fenced code blocks
    (``` ... ``` and ``~~~ ... ~~~``).
    """
    if not text:
        return 0
    prose = _strip_fenced_code(text)
    total = 0
    for pattern in _CAPITAL_I_PATTERNS:
        total += len(pattern.findall(prose))
    total += len(_LOWER_PRONOUN_RE.findall(prose))
    return total


def first_person_per_1k_words(text: str) -> float:
    """Density of first-person pronouns per 1000 words."""
    if not text:
        return 0.0
    prose = _strip_fenced_code(text)
    word_count = len(prose.split())
    if word_count == 0:
        return 0.0
    count = count_first_person(text)
    return count / word_count * 1000


def detect_ai_disclosure(repo_path: Path) -> tuple[bool, str | None, str | None]:
    """Search README and disclosure files for an explicit acknowledgement of AI tool use.

    AI-use disclosure is treated as a positive trust signal: an author who
    documents where AI tools contributed is giving the reviewer useful
    information up front. This function locates such disclosures so they can
    be surfaced rather than inferred.

    Returns ``(found, file_relpath, excerpt)``. ``excerpt`` is the matched
    sentence trimmed to 200 characters, or ``None`` when no disclosure is found.
    """
    if not repo_path.is_dir():
        return False, None, None
    for target in _DISCLOSURE_FILES:
        file_path = _find_disclosure_file(repo_path, target)
        if file_path is None:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        span = _scan_for_disclosure(content)
        if span is None:
            continue
        excerpt = _extract_sentence(content, span[0], span[1])
        try:
            rel = file_path.relative_to(repo_path)
        except ValueError:
            rel = Path(file_path.name)
        return True, str(rel), excerpt
    return False, None, None


def analyze_voice(repo_path: Path) -> VoiceFinding:
    """Combine first-person density and AI-disclosure signals for the repo.

    ``has_first_person_voice`` is ``True`` when the README's first-person
    density exceeds 2 occurrences per 1000 words. This is a presence
    signal, not a quality judgement.
    """
    readme_text = ""
    readme_path = _find_disclosure_file(repo_path, "README.md")
    if readme_path is not None:
        try:
            readme_text = readme_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            readme_text = ""

    count = count_first_person(readme_text)
    density = first_person_per_1k_words(readme_text)
    has_voice = density > _FIRST_PERSON_DENSITY_THRESHOLD

    found, file_rel, excerpt = detect_ai_disclosure(repo_path)

    return VoiceFinding(
        first_person_count=count,
        first_person_per_1k_words=density,
        has_first_person_voice=has_voice,
        ai_disclosure_found=found,
        ai_disclosure_file=file_rel,
        ai_disclosure_excerpt=excerpt,
    )
