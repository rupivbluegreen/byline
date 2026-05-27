"""Tests for byline.fingerprints — pattern-based stylistic-signal scanning per spec §7.2."""

from __future__ import annotations

from pathlib import Path

import pytest

from byline.fingerprints import (
    _load_headers,
    _load_phrases,
    scan_readme,
    scan_repo,
    scan_shell_script,
)
from byline.models import FingerprintHit
from tests.conftest import FIXTURES_DIR


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def test_load_phrases_returns_nonempty_list() -> None:
    phrases = _load_phrases()
    assert isinstance(phrases, list)
    assert len(phrases) > 0
    for entry in phrases:
        assert "pattern" in entry
        assert "type" in entry
        assert entry["type"] in ("literal", "regex")


def test_load_phrases_is_cached() -> None:
    a = _load_phrases()
    b = _load_phrases()
    assert a is b


def test_load_headers_returns_nonempty_list() -> None:
    headers = _load_headers()
    assert isinstance(headers, list)
    assert len(headers) > 0
    for entry in headers:
        assert "pattern" in entry
        assert "type" in entry


# ---------------------------------------------------------------------------
# scan_readme — fixtures
# ---------------------------------------------------------------------------


def test_scan_readme_ai_sample_has_many_hits() -> None:
    text = (FIXTURES_DIR / "ai_readme_sample.md").read_text()
    hits = scan_readme(text, "ai_readme_sample.md")
    assert isinstance(hits, list)
    assert all(isinstance(h, FingerprintHit) for h in hits)
    # Spec requires at least 5 fingerprint hits on the AI sample.
    assert len(hits) >= 5


def test_scan_readme_human_sample_few_hits() -> None:
    text = (FIXTURES_DIR / "human_readme_sample.md").read_text()
    hits = scan_readme(text, "human_readme_sample.md")
    assert len(hits) <= 2


def test_scan_readme_hit_categories_are_valid() -> None:
    text = (FIXTURES_DIR / "ai_readme_sample.md").read_text()
    hits = scan_readme(text, "ai_readme_sample.md")
    allowed = {"phrase", "structure", "shell_banner", "progress_ux", "ai_section_header"}
    for h in hits:
        assert h.category in allowed


def test_scan_readme_file_path_propagated() -> None:
    text = "# Hello\n\nleverage the ecosystem to delve into things.\n"
    hits = scan_readme(text, "some/path/README.md")
    assert hits
    for h in hits:
        assert h.file_path == "some/path/README.md"


def test_scan_readme_phrase_line_numbers_are_one_indexed() -> None:
    text = "intro line\nleverage things here\n"
    hits = scan_readme(text, "x.md")
    phrase_hits = [h for h in hits if h.category == "phrase" and h.pattern == "leverage"]
    assert phrase_hits
    assert phrase_hits[0].line_number == 2


# ---------------------------------------------------------------------------
# em-dash overdose
# ---------------------------------------------------------------------------


def test_em_dash_overdose_signal() -> None:
    # 20 em-dashes in 50 words → density 400.0/1000 words → far above threshold 8.0.
    words = " ".join(f"w{i}" for i in range(50))
    text = words + (" — " * 20)
    hits = scan_readme(text, "dense.md")
    structure = [h for h in hits if h.pattern == "em_dash_overdose"]
    assert len(structure) == 1
    assert structure[0].category == "structure"
    assert structure[0].line_number is None


def test_em_dash_overdose_below_threshold_does_not_fire() -> None:
    # 1 em-dash in 50 words → 20/1000, but density is 1/50*1000 = 20.0 — actually fires.
    # Use a sparse layout: 2 dashes in 500 words → 4.0/1000 < 8.0.
    words = " ".join(f"w{i}" for i in range(500))
    text = words + " — " + " — "
    hits = scan_readme(text, "sparse.md")
    structure = [h for h in hits if h.pattern == "em_dash_overdose"]
    assert structure == []


# ---------------------------------------------------------------------------
# emoji header cluster
# ---------------------------------------------------------------------------


def test_emoji_header_cluster_signal() -> None:
    text = (
        "# 🚀 Launch\n"
        "intro text here\n"
        "## ✨ Sparkle\n"
        "body\n"
        "### 🔥 Fire\n"
        "more body\n"
        "## 🔧 Config\n"
        "config body\n"
        "## 📦 Container\n"
        "container body\n"
    )
    hits = scan_readme(text, "emoji.md")
    cluster = [h for h in hits if h.pattern == "emoji_header_cluster"]
    assert len(cluster) == 1
    assert cluster[0].category == "structure"


def test_emoji_header_cluster_below_threshold() -> None:
    text = "# 🚀 Launch\n## ✨ Sparkle\n### 🔥 Fire\n"
    hits = scan_readme(text, "few.md")
    cluster = [h for h in hits if h.pattern == "emoji_header_cluster"]
    assert cluster == []


# ---------------------------------------------------------------------------
# ai_section_header cluster
# ---------------------------------------------------------------------------


def test_ai_section_header_cluster_signal() -> None:
    # 7 headers from the data file → fires the cluster signal.
    text = (
        "# Getting Started\n"
        "body\n"
        "## Quick Start\n"
        "body\n"
        "## Prerequisites\n"
        "body\n"
        "## Configuration\n"
        "body\n"
        "## Troubleshooting\n"
        "body\n"
        "## Features\n"
        "body\n"
        "## Installation\n"
        "body\n"
    )
    hits = scan_readme(text, "headers.md")
    cluster = [h for h in hits if h.pattern == "ai_section_header_cluster"]
    assert len(cluster) == 1
    assert cluster[0].category == "ai_section_header"
    assert cluster[0].line_number is None


def test_ai_section_header_cluster_below_threshold() -> None:
    text = "# Getting Started\n## Quick Start\n## Prerequisites\n"
    hits = scan_readme(text, "few.md")
    cluster = [h for h in hits if h.pattern == "ai_section_header_cluster"]
    assert cluster == []


# ---------------------------------------------------------------------------
# scan_shell_script
# ---------------------------------------------------------------------------


def test_scan_shell_script_ai_fixture() -> None:
    text = (FIXTURES_DIR / "ai_shell_script.sh").read_text()
    hits = scan_shell_script(text, "ai_shell_script.sh")
    assert isinstance(hits, list)
    banner_hits = [h for h in hits if h.pattern == "banner_comment"]
    progress_hits = [h for h in hits if h.pattern == "progress_marker"]
    assert len(banner_hits) >= 1
    assert len(progress_hits) >= 1
    # Categories should match the spec.
    for h in banner_hits:
        assert h.category == "shell_banner"
    for h in progress_hits:
        assert h.category == "progress_ux"


def test_scan_shell_script_human_fixture_few_hits() -> None:
    text = (FIXTURES_DIR / "human_shell_script.sh").read_text()
    hits = scan_shell_script(text, "human_shell_script.sh")
    assert len(hits) <= 1


def test_scan_shell_script_what_it_does_block() -> None:
    text = "#!/bin/sh\n# WHAT IT DOES:\n# Something useful.\n"
    hits = scan_shell_script(text, "x.sh")
    what = [h for h in hits if h.pattern == "what_it_does_block"]
    assert len(what) == 1
    assert what[0].category == "structure"


def test_scan_shell_script_printf_banner_close() -> None:
    text = "printf 'Setup complete!\\n'\n"
    hits = scan_shell_script(text, "x.sh")
    closers = [h for h in hits if h.pattern == "printf_banner_close"]
    assert len(closers) == 1
    assert closers[0].category == "shell_banner"


# ---------------------------------------------------------------------------
# scan_repo
# ---------------------------------------------------------------------------


def test_scan_repo_synthetic_ai_repo() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    hits = scan_repo(repo)
    assert isinstance(hits, list)
    assert len(hits) >= 3
    paths = {h.file_path for h in hits}
    assert any("README.md" in p for p in paths)
    assert any("setup.sh" in p for p in paths)


def test_scan_repo_paths_are_relative() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    hits = scan_repo(repo)
    for h in hits:
        # Paths should be relative to repo root — never absolute.
        assert not Path(h.file_path).is_absolute()


def test_scan_repo_skips_hidden_and_vendor_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "README.md").write_text("# leverage ecosystem")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "README.md").write_text("# leverage ecosystem")
    (tmp_path / "README.md").write_text("# leverage ecosystem")
    hits = scan_repo(tmp_path)
    for h in hits:
        assert ".git" not in h.file_path
        assert "node_modules" not in h.file_path


def test_scan_repo_skips_oversize_files(tmp_path: Path) -> None:
    big = tmp_path / "huge.md"
    big.write_text("x" * (501 * 1024))
    hits = scan_repo(tmp_path)
    for h in hits:
        assert "huge.md" not in h.file_path


def test_scan_repo_nonexistent_returns_empty(tmp_path: Path) -> None:
    nowhere = tmp_path / "does_not_exist"
    # Documented behaviour: gracefully returns an empty list.
    assert scan_repo(nowhere) == []


def test_scan_repo_handles_dockerfile() -> None:
    repo = FIXTURES_DIR / "synthetic_ai_repo"
    hits = scan_repo(repo)
    docker_hits = [h for h in hits if "Dockerfile" in h.file_path]
    # Dockerfile has banner comments + WHAT IT DOES block.
    assert docker_hits
