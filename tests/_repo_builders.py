"""Helpers for constructing synthetic git repositories for history-forensics tests.

Each builder creates a small git repository under ``tmp_path`` via ``subprocess``
calls to the system ``git`` binary. The shape of the resulting repo (number of
commits, file sizes, timestamps, author identities) is tailored to exercise a
particular branch of the commit-history analysis in ``byline.history``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    """Run a subprocess command, raising on non-zero exit."""

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    subprocess.run(args, cwd=cwd, env=full_env, check=True, capture_output=True)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run ``git`` inside ``repo`` with the given args."""

    _run(["git", "-C", str(repo), *args], cwd=repo, env=env)


def _init_repo(repo: Path, email: str = "alice@example.com", name: str = "Alice") -> None:
    """Initialise a new git repo at ``repo`` with the given default identity."""

    repo.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main", str(repo)], cwd=repo)
    _git(repo, "config", "user.email", email)
    _git(repo, "config", "user.name", name)
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(
    repo: Path,
    message: str,
    when: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> None:
    """Stage everything and create a commit, optionally pinning the timestamp/identity.

    ``when`` should be an ISO-8601 string like ``"2025-01-01T00:00:00Z"``. Both
    author and committer dates are pinned to it so the resulting commit is fully
    deterministic.
    """

    env: dict[str, str] = {}
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    if email is not None:
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_COMMITTER_EMAIL"] = email
    if name is not None:
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_COMMITTER_NAME"] = name

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, env=env or None)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_pasted_repo(tmp_path: Path) -> Path:
    """Repo whose first commit drops in 6 files totalling ~300 lines at once."""

    repo = tmp_path / "pasted"
    _init_repo(repo)

    # README with comfortably more than 200 lines of content so the LOC
    # threshold is met even without the other files.
    readme_lines = ["# Project", ""]
    for i in range(220):
        readme_lines.append(f"This is paragraph {i} of the readme prose body.")
    (repo / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    # Five additional source files so first_commit_file_count >= 5.
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        body = "\n".join(f"# line {i} in {name}" for i in range(20))
        (repo / name).write_text(body + "\n", encoding="utf-8")

    _commit(repo, "Initial commit with everything", when="2025-01-01T00:00:00Z")
    return repo


def build_iterated_repo(tmp_path: Path) -> Path:
    """Repo with 20 small commits spread evenly over a week, single identity."""

    repo = tmp_path / "iterated"
    _init_repo(repo)

    # Seed commit so commits 2..20 each add only a small change.
    (repo / "README.md").write_text("# Project\n\nInitial sketch.\n", encoding="utf-8")
    _commit(repo, "start project", when="2025-01-01T00:00:00Z")

    for i in range(1, 20):
        path = repo / f"file_{i:02d}.txt"
        path.write_text(f"content {i}\n", encoding="utf-8")
        # Step roughly every 8 hours so 20 commits span ~6.3 days.
        hours = i * 8
        day = 1 + hours // 24
        hour = hours % 24
        when = f"2025-01-{day:02d}T{hour:02d}:00:00Z"
        _commit(repo, f"add file {i:02d}", when=when)

    return repo


def build_bursty_repo(tmp_path: Path) -> Path:
    """Repo with 10 commits inside a 30-minute window — heavy burst signal."""

    repo = tmp_path / "bursty"
    _init_repo(repo)

    for i in range(10):
        path = repo / f"step_{i:02d}.txt"
        path.write_text(f"step {i}\n", encoding="utf-8")
        # All within 12:00:00–12:27:00 on the same day.
        minute = i * 3
        when = f"2025-02-01T12:{minute:02d}:00Z"
        _commit(repo, f"step {i:02d}", when=when)

    return repo


def build_drifted_identity_repo(tmp_path: Path) -> Path:
    """Repo whose commits come from three distinct email domains."""

    repo = tmp_path / "drifted"
    _init_repo(repo, email="alice@example.com", name="Alice")

    identities = [
        ("alice@example.com", "Alice"),
        ("bob@other.org", "Bob"),
        ("carol@third.net", "Carol"),
    ]
    for i, (email, name) in enumerate(identities):
        path = repo / f"work_{i}.txt"
        path.write_text(f"work {i}\n", encoding="utf-8")
        when = f"2025-03-{i + 1:02d}T10:00:00Z"
        _commit(repo, f"work piece {i}", when=when, email=email, name=name)

    return repo


def build_repo_with_debug_message(tmp_path: Path) -> Path:
    """Repo containing at least one ``fix typo`` style debug commit message."""

    repo = tmp_path / "debug"
    _init_repo(repo)

    (repo / "README.md").write_text("# Hello\n", encoding="utf-8")
    _commit(repo, "initial draft of the project", when="2025-04-01T09:00:00Z")

    (repo / "README.md").write_text("# Hello world\n", encoding="utf-8")
    _commit(repo, "fix typo in heading", when="2025-04-01T10:00:00Z")

    (repo / "README.md").write_text("# Hello world!\n", encoding="utf-8")
    _commit(repo, "wip: tinkering", when="2025-04-01T11:00:00Z")

    return repo
