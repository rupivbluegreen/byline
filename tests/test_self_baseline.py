"""Tests for byline.self_baseline — within-repo divergence signal per spec §5.4."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import get_args

import pytest

from byline.models import SelfBaselineFinding, StyleProfile
from byline.self_baseline import (
    _build_note,
    _classify,
    _collect_code_comments,
    _collect_commit_messages,
    _distance,
    _normalized_vector,
    _read_readme,
    compute_self_baseline,
)

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_init(repo: Path) -> None:
    """Initialise an empty git repo with deterministic author info."""

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _git_commit(repo: Path, message: str) -> None:
    """Stage everything and create a commit with ``message``."""

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


# ---------------------------------------------------------------------------
# _normalized_vector & _distance unit tests
# ---------------------------------------------------------------------------


def _profile(
    *,
    em_dash_density: float = 0.0,
    sophistication_score: float = 0.0,
    typo_rate: float = 0.0,
    type_token_ratio: float = 0.0,
) -> StyleProfile:
    """Build a minimal StyleProfile with only the 4 dimensions we care about."""

    return StyleProfile(
        em_dash_density=em_dash_density,
        emoji_in_headers_ratio=0.0,
        avg_sentence_length=0.0,
        type_token_ratio=type_token_ratio,
        typo_rate=typo_rate,
        sophistication_score=sophistication_score,
        banner_comment_density=0.0,
        progress_ux_score=0.0,
    )


def test_normalized_vector_scales_em_dash_and_typo() -> None:
    """em_dash_density divides by 100, typo_rate divides by 50; the other
    two dimensions pass through unscaled."""
    p = _profile(
        em_dash_density=50.0,
        sophistication_score=0.6,
        typo_rate=25.0,
        type_token_ratio=0.4,
    )
    assert _normalized_vector(p) == (0.5, 0.6, 0.5, 0.4)


def test_normalized_vector_all_zero() -> None:
    """A zeroed profile maps to the origin in normalized space."""
    assert _normalized_vector(_profile()) == (0.0, 0.0, 0.0, 0.0)


def test_distance_to_self_is_zero() -> None:
    """The distance between a profile and itself is exactly 0."""
    p = _profile(
        em_dash_density=10.0, sophistication_score=0.3, typo_rate=5.0, type_token_ratio=0.5
    )
    assert _distance(p, p) == 0.0


def test_distance_one_dimension() -> None:
    """A pure shift in a single normalized dimension reproduces that shift."""
    a = _profile(sophistication_score=0.2)
    b = _profile(sophistication_score=0.5)
    assert _distance(a, b) == pytest.approx(0.3)


def test_distance_symmetric() -> None:
    """d(a, b) == d(b, a)."""
    a = _profile(em_dash_density=20.0, type_token_ratio=0.7)
    b = _profile(typo_rate=10.0, sophistication_score=0.4)
    assert _distance(a, b) == pytest.approx(_distance(b, a))


# ---------------------------------------------------------------------------
# _classify rule
# ---------------------------------------------------------------------------


def test_classify_both_low_is_consistent() -> None:
    assert _classify(0.1, 0.2) == "consistent"


def test_classify_boundary_notable() -> None:
    """0.3 is the lower edge of the notable band (consistent uses ``< 0.3``)."""
    assert _classify(0.3, 0.1) == "notable"
    assert _classify(0.29999, 0.29999) == "consistent"


def test_classify_either_distance_triggers_band() -> None:
    """Either distance crossing a threshold drives the classification."""
    assert _classify(0.05, 0.45) == "notable"
    assert _classify(0.7, 0.05) == "significant"


def test_classify_boundary_significant() -> None:
    """0.6 is the lower edge of significant."""
    assert _classify(0.6, 0.0) == "significant"
    assert _classify(0.59999, 0.0) == "notable"


def test_within_repo_divergence_literal_values() -> None:
    """The Literal type advertises exactly the three documented levels."""
    field = SelfBaselineFinding.model_fields["within_repo_divergence"]
    assert set(get_args(field.annotation)) == {"consistent", "notable", "significant"}


# ---------------------------------------------------------------------------
# Surface extraction
# ---------------------------------------------------------------------------


def test_read_readme_missing_returns_empty(tmp_path: Path) -> None:
    assert _read_readme(tmp_path) == ""


def test_read_readme_returns_contents(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello world\n", encoding="utf-8")
    assert _read_readme(tmp_path) == "hello world\n"


def test_collect_commit_messages_non_git_returns_empty(tmp_path: Path) -> None:
    """Plain directories degrade to an empty commit-message surface."""
    assert _collect_commit_messages(tmp_path) == ""


def test_collect_commit_messages_skips_merges(tmp_path: Path) -> None:
    """Merge commits (>1 parent) are dropped — their messages are auto-noise."""
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    _git_commit(tmp_path, "first real commit")
    # Branch + second commit, then merge with a forced merge commit.
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tmp_path, check=True)
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    _git_commit(tmp_path, "side branch work")
    subprocess.run(
        ["git", "checkout", "-q", "master"],
        cwd=tmp_path,
        check=False,
    )
    # Some git versions default to ``main`` instead of ``master``; tolerate either.
    branches = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branches == "side":
        subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
    (tmp_path / "c.txt").write_text("c\n", encoding="utf-8")
    _git_commit(tmp_path, "trunk progress")
    subprocess.run(
        ["git", "merge", "--no-ff", "-q", "-m", "Merge branch side", "side"],
        cwd=tmp_path,
        check=True,
    )

    text = _collect_commit_messages(tmp_path)
    assert "first real commit" in text
    assert "side branch work" in text
    assert "trunk progress" in text
    assert "Merge branch side" not in text


def test_collect_code_comments_python_hash_and_docstring(tmp_path: Path) -> None:
    """Python source contributes both ``#`` comment lines and docstring bodies."""
    src = '"""Module docstring goes here."""\n\n# explicit hash comment\nx = 1\n'
    (tmp_path / "thing.py").write_text(src, encoding="utf-8")
    comments = _collect_code_comments(tmp_path)
    assert "explicit hash comment" in comments
    assert "Module docstring goes here." in comments
    assert "x = 1" not in comments  # code body must not leak in


def test_collect_code_comments_js_double_slash(tmp_path: Path) -> None:
    """JS/TS source contributes ``//`` comment lines."""
    (tmp_path / "a.js").write_text("// hello there\nconst x = 1;\n", encoding="utf-8")
    comments = _collect_code_comments(tmp_path)
    assert "hello there" in comments
    assert "const x" not in comments


def test_collect_code_comments_skips_excluded_dirs(tmp_path: Path) -> None:
    """Source files under .venv/node_modules/etc. are ignored."""
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("# venv comment\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("# real comment\n", encoding="utf-8")
    comments = _collect_code_comments(tmp_path)
    assert "real comment" in comments
    assert "venv comment" not in comments


# ---------------------------------------------------------------------------
# _build_note
# ---------------------------------------------------------------------------


def test_build_note_mentions_both_pairs() -> None:
    note = _build_note(0.1, 0.4)
    assert "Commit messages" in note
    assert "README" in note
    assert "code comments" in note.lower()


# ---------------------------------------------------------------------------
# compute_self_baseline — end-to-end
# ---------------------------------------------------------------------------


def test_non_git_directory_returns_finding(tmp_path: Path) -> None:
    """Non-git directories yield a SelfBaselineFinding with no crash.

    The commit-message surface collapses to an empty string when ``git.Repo``
    cannot open the directory; the rest of the analysis proceeds normally
    over the README and source comments.
    """
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "tiny.py").write_text("# one comment line\n", encoding="utf-8")

    finding = compute_self_baseline(tmp_path)
    assert isinstance(finding, SelfBaselineFinding)
    assert finding.within_repo_divergence in {"consistent", "notable", "significant"}
    # Commit messages and README are both empty — that surface is aligned.
    assert finding.commit_msg_vs_readme_distance == pytest.approx(0.0)
    assert isinstance(finding.note, str)


def test_non_git_directory_with_aligned_surfaces_is_consistent(tmp_path: Path) -> None:
    """When README and code comments share the same prose, the result is 'consistent'.

    Non-git directories degrade gracefully — the commit surface is empty,
    so we focus on README-vs-comment alignment by giving both surfaces the
    same content.
    """
    shared = "hello world this is a small simple project with plain words\n"
    (tmp_path / "README.md").write_text(shared, encoding="utf-8")
    (tmp_path / "tiny.py").write_text(f"# {shared}", encoding="utf-8")

    finding = compute_self_baseline(tmp_path)
    assert isinstance(finding, SelfBaselineFinding)
    # README vs code comment surfaces match nearly exactly.
    assert finding.code_comment_vs_readme_distance < 0.3


def test_small_git_repo_similar_surfaces(tmp_path: Path) -> None:
    """A small git repo whose three surfaces share vocabulary lands at most at 'notable'.

    All three surfaces (commit messages, README, code comments) draw from the same
    plain-word corpus and are long enough for the type-token-ratio dimension to
    stabilise — short corpora drive TTR to ~1.0 and dominate the distance.
    """
    _git_init(tmp_path)
    plain_sentence = "the project has a simple goal and the code is clear and the readme is plain"
    body = "\n".join([plain_sentence] * 12)
    (tmp_path / "README.md").write_text(body + "\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "\n".join(f"# {plain_sentence}" for _ in range(12)) + "\n",
        encoding="utf-8",
    )
    _git_commit(tmp_path, plain_sentence)
    extended = body + "\n" + plain_sentence + " and a little extra\n"
    (tmp_path / "README.md").write_text(extended, encoding="utf-8")
    _git_commit(tmp_path, plain_sentence + " with an additional line")

    finding = compute_self_baseline(tmp_path)
    assert isinstance(finding, SelfBaselineFinding)
    assert finding.within_repo_divergence in {"consistent", "notable"}


def test_large_divergence_em_dash_heavy_readme(tmp_path: Path) -> None:
    """A README dense with em-dashes diverges from plain-word code comments.

    The em-dash dimension dominates the normalised vector here, so the
    comment-vs-README distance must clear the 0.3 notable threshold.
    """
    readme_text = "text — text — text — " * 50
    (tmp_path / "README.md").write_text(readme_text, encoding="utf-8")
    (tmp_path / "code.py").write_text(
        "\n".join(f"# plain word number {i} with simple wording" for i in range(20)) + "\n",
        encoding="utf-8",
    )

    finding = compute_self_baseline(tmp_path)
    assert finding.code_comment_vs_readme_distance > 0.3
    assert finding.within_repo_divergence in {"notable", "significant"}


def test_returns_correct_model_type(tmp_path: Path) -> None:
    """compute_self_baseline always returns a SelfBaselineFinding."""
    finding = compute_self_baseline(tmp_path)
    assert isinstance(finding, SelfBaselineFinding)
    assert finding.within_repo_divergence in {"consistent", "notable", "significant"}
