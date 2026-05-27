"""Tests for byline.boilerplate — meta-file density signal per spec §5.6."""

from __future__ import annotations

from pathlib import Path

from byline.boilerplate import META_FILES_CHECKED, analyze_boilerplate
from byline.models import BoilerplateFinding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch_meta(repo: Path, entry: str) -> None:
    """Create the meta-file ``entry`` (file or directory) under ``repo``."""

    target = repo / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    if "/" in entry and not entry.endswith(".md") and not entry.endswith(".yaml"):
        # Directory-style entry (e.g. ``.github/ISSUE_TEMPLATE``): create the
        # directory and drop a single placeholder file inside so it counts.
        target.mkdir(parents=True, exist_ok=True)
        (target / "bug_report.md").write_text("placeholder", encoding="utf-8")
    else:
        target.write_text("placeholder", encoding="utf-8")


def _write_loc(repo: Path, filename: str, n_lines: int) -> None:
    """Drop a ``.py`` file with ``n_lines`` lines of trivial content."""

    body = "\n".join(f"x = {i}" for i in range(n_lines))
    (repo / filename).write_text(body + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Density / severity behaviour
# ---------------------------------------------------------------------------


def test_empty_repo_density_zero_normal(tmp_path: Path) -> None:
    """Empty repo: density 0.0, severity 'normal' — the LOC bump does not
    apply when there are zero source files (the heuristic is meaningless
    without a codebase to weigh)."""
    finding = analyze_boilerplate(tmp_path)
    assert isinstance(finding, BoilerplateFinding)
    assert finding.density_ratio == 0.0
    assert finding.meta_files_present == []
    assert finding.severity == "normal"


def test_low_density_large_repo_no_bump(tmp_path: Path) -> None:
    """2 of 10 meta files + a 2000-line .py file -> density 0.2, normal, no bump."""
    _touch_meta(tmp_path, ".editorconfig")
    _touch_meta(tmp_path, "CONTRIBUTING.md")
    _write_loc(tmp_path, "main.py", 2000)

    finding = analyze_boilerplate(tmp_path)
    assert finding.density_ratio == 0.2
    assert finding.severity == "normal"
    assert set(finding.meta_files_present) == {".editorconfig", "CONTRIBUTING.md"}


def test_mid_density_large_repo_notable_no_bump(tmp_path: Path) -> None:
    """5 of 10 + 2000-line .py -> density 0.5, notable, no bump (loc >= 1000)."""
    for entry in (
        ".editorconfig",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        ".gitattributes",
    ):
        _touch_meta(tmp_path, entry)
    _write_loc(tmp_path, "main.py", 2000)

    finding = analyze_boilerplate(tmp_path)
    assert finding.density_ratio == 0.5
    assert finding.severity == "notable"


def test_mid_density_small_repo_bumped_to_significant(tmp_path: Path) -> None:
    """5 of 10 + 100-line .py -> density 0.5, bumped from notable to significant."""
    for entry in (
        ".editorconfig",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        ".gitattributes",
    ):
        _touch_meta(tmp_path, entry)
    _write_loc(tmp_path, "main.py", 100)

    finding = analyze_boilerplate(tmp_path)
    assert finding.density_ratio == 0.5
    assert finding.severity == "significant"


def test_high_density_large_repo_significant_no_bump_effect(tmp_path: Path) -> None:
    """8 of 10 + 5000-line .py -> density 0.8, significant (already max)."""
    for entry in (
        ".editorconfig",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/CODEOWNERS",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        ".gitattributes",
    ):
        _touch_meta(tmp_path, entry)
    _write_loc(tmp_path, "main.py", 5000)

    finding = analyze_boilerplate(tmp_path)
    assert finding.density_ratio == 0.8
    assert finding.severity == "significant"


# ---------------------------------------------------------------------------
# Directory-entry handling
# ---------------------------------------------------------------------------


def test_issue_template_directory_with_file_counts_as_present(tmp_path: Path) -> None:
    """``.github/ISSUE_TEMPLATE`` with at least one file inside counts as present."""
    issue_template = tmp_path / ".github" / "ISSUE_TEMPLATE"
    issue_template.mkdir(parents=True)
    (issue_template / "bug_report.md").write_text("placeholder", encoding="utf-8")

    finding = analyze_boilerplate(tmp_path)
    assert ".github/ISSUE_TEMPLATE" in finding.meta_files_present


def test_issue_template_directory_empty_does_not_count(tmp_path: Path) -> None:
    """An empty ``.github/ISSUE_TEMPLATE`` directory is NOT counted as present."""
    (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)

    finding = analyze_boilerplate(tmp_path)
    assert ".github/ISSUE_TEMPLATE" not in finding.meta_files_present


# ---------------------------------------------------------------------------
# Shape contracts
# ---------------------------------------------------------------------------


def test_present_is_sublist_of_checked(tmp_path: Path) -> None:
    """Every entry in ``meta_files_present`` must come from ``META_FILES_CHECKED``."""
    _touch_meta(tmp_path, ".editorconfig")
    _touch_meta(tmp_path, "CONTRIBUTING.md")
    _touch_meta(tmp_path, ".gitattributes")

    finding = analyze_boilerplate(tmp_path)
    for entry in finding.meta_files_present:
        assert entry in META_FILES_CHECKED
    assert finding.meta_files_checked == META_FILES_CHECKED


def test_checked_list_matches_module_constant(tmp_path: Path) -> None:
    """The returned ``meta_files_checked`` mirrors the canonical reference list."""
    finding = analyze_boilerplate(tmp_path)
    assert finding.meta_files_checked == META_FILES_CHECKED
