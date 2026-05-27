"""Tests for byline.voice — first-person voice and AI-disclosure signals per spec §5.5."""

from __future__ import annotations

from pathlib import Path

from byline.models import VoiceFinding
from byline.voice import (
    analyze_voice,
    count_first_person,
    detect_ai_disclosure,
    first_person_per_1k_words,
)
from tests.conftest import FIXTURES_DIR

DISCLOSED_REPO = FIXTURES_DIR / "disclosed_ai_repo"
SYNTHETIC_REPO = FIXTURES_DIR / "synthetic_ai_repo"


# ---------------------------------------------------------------------------
# count_first_person
# ---------------------------------------------------------------------------


def test_count_first_person_basic() -> None:
    assert count_first_person("I went to the store with my dog") == 2


def test_count_first_person_no_match() -> None:
    assert count_first_person("we went to the store") == 0


def test_count_first_person_contractions() -> None:
    assert count_first_person("I'm building it for me") == 2


def test_count_first_person_skips_fenced_code() -> None:
    text = "text with ``` I am inside ``` outside"
    assert count_first_person(text) == 0


def test_count_first_person_capital_sensitivity() -> None:
    # Lowercase standalone 'i' should not match (it's typically a variable name, etc.)
    assert count_first_person("i live") == 0


def test_count_first_person_skips_tilde_fenced_code() -> None:
    text = "outside ~~~\nI am inside\n~~~ also outside"
    assert count_first_person(text) == 0


def test_count_first_person_case_insensitive_my_me_mine() -> None:
    # 'my', 'mine', 'me' are case-insensitive.
    assert count_first_person("My ideas are mine, not yours; give them to ME") == 3


# ---------------------------------------------------------------------------
# first_person_per_1k_words
# ---------------------------------------------------------------------------


def test_first_person_per_1k_words_density() -> None:
    # Build a text of exactly 100 words containing 4 first-person pronouns.
    words = ["I", "have", "my", "ideas", "and"] + ["word"] * 95
    # That has 2 first-person tokens (I, my). Bump it up to 4.
    words = ["I", "love", "my", "code", "I", "wrote", "for", "me", "okay"] + ["word"] * 91
    text = " ".join(words)
    # Count: I (x2), my (x1), me (x1) = 4
    assert count_first_person(text) == 4
    assert len(text.split()) == 100
    assert first_person_per_1k_words(text) == 40.0


def test_first_person_per_1k_words_empty() -> None:
    assert first_person_per_1k_words("") == 0.0


# ---------------------------------------------------------------------------
# detect_ai_disclosure
# ---------------------------------------------------------------------------


def test_detect_ai_disclosure_finds_disclosed_repo() -> None:
    found, file_rel, excerpt = detect_ai_disclosure(DISCLOSED_REPO)
    assert found is True
    assert file_rel is not None
    assert excerpt is not None
    assert len(excerpt) > 0
    assert "Claude" in excerpt or "AI" in excerpt or "ai" in excerpt.lower()


def test_detect_ai_disclosure_synthetic_repo_no_disclosure() -> None:
    found, file_rel, excerpt = detect_ai_disclosure(SYNTHETIC_REPO)
    assert found is False
    assert file_rel is None
    assert excerpt is None


def test_detect_ai_disclosure_empty_dir(tmp_path: Path) -> None:
    found, file_rel, excerpt = detect_ai_disclosure(tmp_path)
    assert found is False
    assert file_rel is None
    assert excerpt is None


# ---------------------------------------------------------------------------
# analyze_voice
# ---------------------------------------------------------------------------


def test_analyze_voice_on_disclosed_repo() -> None:
    finding = analyze_voice(DISCLOSED_REPO)
    assert isinstance(finding, VoiceFinding)
    assert finding.ai_disclosure_found is True
    assert finding.has_first_person_voice is True
    assert finding.first_person_count > 0
    assert finding.first_person_per_1k_words > 2.0
    assert finding.ai_disclosure_file is not None
    assert finding.ai_disclosure_excerpt is not None


def test_analyze_voice_empty_dir(tmp_path: Path) -> None:
    finding = analyze_voice(tmp_path)
    assert isinstance(finding, VoiceFinding)
    assert finding.first_person_count == 0
    assert finding.first_person_per_1k_words == 0.0
    assert finding.has_first_person_voice is False
    assert finding.ai_disclosure_found is False
    assert finding.ai_disclosure_file is None
    assert finding.ai_disclosure_excerpt is None
