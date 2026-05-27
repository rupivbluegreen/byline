"""Shared pytest configuration and fixtures."""

from pathlib import Path

import pytest

from tests._repo_builders import (
    build_bursty_repo,
    build_drifted_identity_repo,
    build_iterated_repo,
    build_pasted_repo,
    build_repo_with_debug_message,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def pasted_repo(tmp_path: Path) -> Path:
    """A repo whose first commit has 6 files and ~300 lines, dropped in at once."""

    return build_pasted_repo(tmp_path)


@pytest.fixture
def iterated_repo(tmp_path: Path) -> Path:
    """A repo with 20 small commits spread over roughly a week."""

    return build_iterated_repo(tmp_path)


@pytest.fixture
def bursty_repo(tmp_path: Path) -> Path:
    """A repo with 10 commits all within a 30-minute window."""

    return build_bursty_repo(tmp_path)


@pytest.fixture
def drifted_identity_repo(tmp_path: Path) -> Path:
    """A repo whose commits span three distinct email domains."""

    return build_drifted_identity_repo(tmp_path)


@pytest.fixture
def debug_message_repo(tmp_path: Path) -> Path:
    """A repo whose commit messages include `fix typo` / `wip` style entries."""

    return build_repo_with_debug_message(tmp_path)
