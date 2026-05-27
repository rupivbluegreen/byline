"""Tests for byline.metrics — stylistic signal computations per spec §7.1."""

from __future__ import annotations

from pathlib import Path

import pytest

from byline.metrics import (
    _is_emoji,
    avg_sentence_length,
    banner_comment_density,
    em_dash_density,
    emoji_in_headers_ratio,
    metrics_for_text,
    progress_ux_score,
    sophistication_score,
    type_token_ratio,
    typo_rate,
)
from byline.models import StyleProfile

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# em_dash_density
# ---------------------------------------------------------------------------


def test_em_dash_density_unicode_dash() -> None:
    # "a — b — c — d" — 4 words, 3 em-dashes → 750.0 per 1000 words
    assert em_dash_density("a — b — c — d") == pytest.approx(750.0)


def test_em_dash_density_empty_string() -> None:
    assert em_dash_density("") == 0.0


def test_em_dash_density_double_hyphen() -> None:
    # "a -- b -- c" — 3 words (split on whitespace), 2 double-hyphens → 666.67
    assert em_dash_density("a -- b -- c") == pytest.approx(2 / 3 * 1000)


def test_em_dash_density_no_dashes() -> None:
    assert em_dash_density("a b c d") == 0.0


def test_em_dash_density_mixed() -> None:
    # Mix unicode and double-hyphen
    text = "alpha — beta -- gamma"  # 3 words, 1 unicode + 1 double-hyphen = 2
    assert em_dash_density(text) == pytest.approx(2 / 3 * 1000)


# ---------------------------------------------------------------------------
# emoji_in_headers_ratio
# ---------------------------------------------------------------------------


def test_emoji_in_headers_ratio_ai_sample_fixture() -> None:
    text = (FIXTURES_DIR / "ai_readme_sample.md").read_text()
    ratio = emoji_in_headers_ratio(text)
    assert ratio > 0.5


def test_emoji_in_headers_ratio_no_emoji_headers() -> None:
    text = "# Plain Title\n## Another One\n### And Another\n"
    assert emoji_in_headers_ratio(text) == 0.0


def test_emoji_in_headers_ratio_all_emoji_headers() -> None:
    text = "# 🚀 Launch\n## ✨ Sparkle\n### 🔥 Fire\n"
    assert emoji_in_headers_ratio(text) == 1.0


def test_emoji_in_headers_ratio_no_headers() -> None:
    assert emoji_in_headers_ratio("just some prose\nno headers here") == 0.0


def test_emoji_in_headers_ratio_mixed() -> None:
    text = "# 🚀 Yes\n## No\n### 🔧 Yes\n#### No\n"
    assert emoji_in_headers_ratio(text) == pytest.approx(0.5)


def test_is_emoji_covers_ranges() -> None:
    assert _is_emoji("🚀")  # U+1F680
    assert _is_emoji("✨")  # U+2728 Dingbat
    assert _is_emoji("⚙")  # U+2699 Misc Symbol
    assert _is_emoji("⌚")  # U+231A Misc Technical
    assert not _is_emoji("a")
    assert not _is_emoji("#")
    assert not _is_emoji(" ")


# ---------------------------------------------------------------------------
# avg_sentence_length
# ---------------------------------------------------------------------------


def test_avg_sentence_length_empty() -> None:
    assert avg_sentence_length("") == 0.0


def test_avg_sentence_length_nonzero_for_real_text() -> None:
    text = "This is a sentence. Here is another one. And a third with several words in it."
    result = avg_sentence_length(text)
    assert result > 0.0


# ---------------------------------------------------------------------------
# type_token_ratio
# ---------------------------------------------------------------------------


def test_type_token_ratio_repeated() -> None:
    assert type_token_ratio("the the the") == pytest.approx(1 / 3)


def test_type_token_ratio_unique() -> None:
    assert type_token_ratio("a b c d") == pytest.approx(1.0)


def test_type_token_ratio_empty() -> None:
    assert type_token_ratio("") == 0.0


def test_type_token_ratio_punctuation_stripped() -> None:
    # "Hello, hello!" → ["hello", "hello"] → 0.5
    assert type_token_ratio("Hello, hello!") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# sophistication_score
# ---------------------------------------------------------------------------


def test_sophistication_score_all_common_words_low() -> None:
    # All super-common words → low score
    score = sophistication_score("the cat sat on the mat")
    assert score < 0.2


def test_sophistication_score_rare_terms_higher() -> None:
    text = "heteroskedasticity stochastic perturbation eigenvector orthonormalization"
    score = sophistication_score(text)
    assert score > 0.5


def test_sophistication_score_empty() -> None:
    assert sophistication_score("") == 0.0


# ---------------------------------------------------------------------------
# banner_comment_density
# ---------------------------------------------------------------------------


def test_banner_comment_density_one_banner_in_ten_lines() -> None:
    lines = ["x"] * 9 + ["# ====="]
    text = "\n".join(lines)
    assert banner_comment_density(text) == pytest.approx(10.0)


def test_banner_comment_density_no_banners() -> None:
    text = "x\n" * 10
    assert banner_comment_density(text) == 0.0


def test_banner_comment_density_empty() -> None:
    assert banner_comment_density("") == 0.0


def test_banner_comment_density_various_separators() -> None:
    # Banner styles: # =====, #####, # -----
    text = "# =====\n#####\n# -----\nfoo\nbar\nbaz\nquux\nplugh\nxyzzy\nblah"
    # 3 banner lines / 10 lines * 100 = 30.0
    assert banner_comment_density(text) == pytest.approx(30.0)


def test_banner_comment_density_ai_shell_fixture() -> None:
    text = (FIXTURES_DIR / "ai_shell_script.sh").read_text()
    # Fixture is loaded with banner comments
    assert banner_comment_density(text) > 0.0


# ---------------------------------------------------------------------------
# progress_ux_score
# ---------------------------------------------------------------------------


def test_progress_ux_score_zero_signals() -> None:
    assert progress_ux_score("just some plain text") == 0.0


def test_progress_ux_score_two_signals() -> None:
    # 2 signals → 2/4 = 0.5
    text = "Running [1/3] step.\nExpected output: success."
    assert progress_ux_score(text) == pytest.approx(0.5)


def test_progress_ux_score_four_signals_saturates() -> None:
    text = (
        "Step [1/4] starting.\n"
        "Expected output: ok.\n"
        "Next steps: continue.\n"
        "What this does: a thing.\n"
    )
    assert progress_ux_score(text) == 1.0


def test_progress_ux_score_more_than_four_signals_saturates() -> None:
    text = (
        "[1/2] go\n"
        "Expected output: ok\n"
        "Next steps: more\n"
        "What this does: thing\n"
        "When to run: now\n"
        "Setup complete!\n"
        "Cleanup complete!\n"
    )
    assert progress_ux_score(text) == 1.0


def test_progress_ux_score_case_insensitive() -> None:
    text = "EXPECTED OUTPUT: ok\nNEXT STEPS: go"
    assert progress_ux_score(text) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# typo_rate
# ---------------------------------------------------------------------------


def test_typo_rate_empty_returns_zero() -> None:
    # Empty input always returns 0.0 (word_count is 0), no LanguageTool needed.
    assert typo_rate("") == 0.0


def test_typo_rate_tolerates_unavailable_tool() -> None:
    # If LanguageTool/JVM is unavailable, typo_rate should return 0.0 (logged warning).
    # If it IS available, it should return a value >= 0 for clean text.
    # We don't require a typo because that depends on the LT environment; we just
    # check the function does not raise and returns a non-negative float.
    text = "this is a sentance with a tpyo in it"
    rate = typo_rate(text)
    assert isinstance(rate, float)
    assert rate >= 0.0


# ---------------------------------------------------------------------------
# metrics_for_text
# ---------------------------------------------------------------------------


def test_metrics_for_text_prose_returns_style_profile() -> None:
    text = "This is a sample sentence — with an em-dash. The cat sat on the mat."
    profile = metrics_for_text(text, kind="prose")
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density > 0.0
    assert profile.avg_sentence_length > 0.0
    assert profile.type_token_ratio > 0.0
    assert profile.banner_comment_density == 0.0
    assert profile.progress_ux_score == 0.0


def test_metrics_for_text_prose_markdown_emojis() -> None:
    text = (FIXTURES_DIR / "ai_readme_sample.md").read_text()
    profile = metrics_for_text(text, kind="prose")
    assert isinstance(profile, StyleProfile)
    assert profile.emoji_in_headers_ratio > 0.0
    assert profile.em_dash_density > 0.0


def test_metrics_for_text_shell_zeros_prose_metrics() -> None:
    text = (FIXTURES_DIR / "ai_shell_script.sh").read_text()
    profile = metrics_for_text(text, kind="shell")
    assert isinstance(profile, StyleProfile)
    assert profile.em_dash_density == 0.0
    assert profile.emoji_in_headers_ratio == 0.0
    assert profile.avg_sentence_length == 0.0
    assert profile.type_token_ratio == 0.0
    assert profile.typo_rate == 0.0
    assert profile.sophistication_score == 0.0
    # Shell-only metrics:
    assert profile.banner_comment_density > 0.0
    assert profile.progress_ux_score > 0.0
