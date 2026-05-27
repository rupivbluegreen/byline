"""Tests for byline.repo_fetcher — local clone helper per spec §5.1."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from byline.repo_fetcher import cloned_repo, detect_default_branch, is_local_path

# ---------------------------------------------------------------------------
# is_local_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/tmp/x", True),
        ("./x", True),
        ("../x", True),
        (".", True),
        ("~/projects/foo", True),
        ("relative/path/no/scheme", True),
        ("https://github.com/foo/bar", False),
        ("http://github.com/foo/bar", False),
        ("git@github.com:foo/bar.git", False),
        ("github.com/foo/bar", False),
    ],
)
def test_is_local_path(target: str, expected: bool) -> None:
    assert is_local_path(target) is expected


# ---------------------------------------------------------------------------
# detect_default_branch
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _init_repo_with_commit(path: Path) -> None:
    """Initialize a git repo at `path` with one commit on whatever default branch
    git uses (main or master, depending on user config)."""
    _run(["git", "init", "--quiet"], cwd=path)
    _run(["git", "config", "user.email", "test@example.com"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    _run(["git", "config", "commit.gpgsign", "false"], cwd=path)
    (path / "README.md").write_text("hello\n")
    _run(["git", "add", "README.md"], cwd=path)
    _run(["git", "commit", "--quiet", "-m", "initial"], cwd=path)


def test_detect_default_branch_returns_current_branch(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    branch = detect_default_branch(tmp_path)
    # Depending on the host's git defaults, the initial branch could be `main`
    # or `master`. Both are valid here — we just need a non-empty real name.
    assert branch in {"main", "master"}


def test_detect_default_branch_falls_back_to_main_on_detached_head(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    # Detach HEAD by checking out the commit SHA directly.
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    _run(["git", "checkout", "--quiet", "--detach", sha], cwd=tmp_path)
    assert detect_default_branch(tmp_path) == "main"


def test_detect_default_branch_falls_back_to_main_on_non_repo(tmp_path: Path) -> None:
    # An empty directory is not a git repo at all — must not raise.
    assert detect_default_branch(tmp_path) == "main"


# ---------------------------------------------------------------------------
# cloned_repo
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_repo_url(tmp_path: Path) -> str:
    """Create a real local bare repo seeded with one commit and return its file:// URL."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _run(["git", "init", "--bare", "--quiet"], cwd=bare)

    work = tmp_path / "work"
    work.mkdir()
    _run(["git", "init", "--quiet"], cwd=work)
    _run(["git", "config", "user.email", "test@example.com"], cwd=work)
    _run(["git", "config", "user.name", "Test"], cwd=work)
    _run(["git", "config", "commit.gpgsign", "false"], cwd=work)
    (work / "hello.txt").write_text("hello from byline\n")
    _run(["git", "add", "hello.txt"], cwd=work)
    _run(["git", "commit", "--quiet", "-m", "seed"], cwd=work)
    # Push to bare; let git pick whatever branch is current.
    _run(["git", "remote", "add", "origin", str(bare)], cwd=work)
    _run(["git", "push", "--quiet", "origin", "HEAD"], cwd=work)

    return f"file://{bare}"


def test_cloned_repo_yields_existing_path_with_content(bare_repo_url: str) -> None:
    captured_path: Path | None = None
    with cloned_repo(bare_repo_url) as repo_path:
        captured_path = repo_path
        assert repo_path.exists()
        assert repo_path.is_dir()
        assert (repo_path / "hello.txt").exists()
        assert (repo_path / "hello.txt").read_text() == "hello from byline\n"
        # git metadata should be present (no --bare on clone)
        assert (repo_path / ".git").exists()

    # After context exit, the tempdir must be gone.
    assert captured_path is not None
    assert not captured_path.exists()


def test_cloned_repo_respects_depth(bare_repo_url: str) -> None:
    with cloned_repo(bare_repo_url, depth=1) as repo_path:
        assert (repo_path / "hello.txt").exists()


def test_cloned_repo_raises_runtime_error_on_failure(tmp_path: Path) -> None:
    bogus_url = f"file://{tmp_path}/does-not-exist.git"
    with pytest.raises(RuntimeError):
        with cloned_repo(bogus_url):
            pass


def test_cloned_repo_does_not_log_token(
    bare_repo_url: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # We can't exercise the token rewrite end-to-end against a file:// URL because
    # the rewrite only applies to https://. But we can still assert that an
    # explicitly-set token never lands in log output for whatever URL we use.
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_super_secret_token_value")
    caplog.set_level(logging.INFO, logger="byline.repo_fetcher")
    with cloned_repo(bare_repo_url):
        pass

    for record in caplog.records:
        assert "ghp_super_secret_token_value" not in record.getMessage()


def test_cloned_repo_rewrites_https_url_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The internal URL builder must embed GITHUB_TOKEN for https:// URLs only."""
    from byline.repo_fetcher import _build_clone_url

    monkeypatch.setenv("GITHUB_TOKEN", "tkn123")
    assert _build_clone_url("https://github.com/foo/bar") == "https://tkn123@github.com/foo/bar"
    # file:// must be left alone
    assert _build_clone_url("file:///tmp/x.git") == "file:///tmp/x.git"
    # ssh must be left alone
    assert _build_clone_url("git@github.com:foo/bar.git") == "git@github.com:foo/bar.git"

    # No token => no rewrite
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _build_clone_url("https://github.com/foo/bar") == "https://github.com/foo/bar"


def test_cloned_repo_sets_no_terminal_prompt_env(
    bare_repo_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIT_TERMINAL_PROMPT=0 must be set in the clone subprocess env."""
    import git

    captured_env: dict[str, str] = {}
    original = git.Repo.clone_from

    def spy(url, to_path, **kwargs):  # type: ignore[no-untyped-def]
        env = kwargs.get("env") or {}
        captured_env.update(env)
        return original(url, to_path, **kwargs)

    monkeypatch.setattr(git.Repo, "clone_from", spy)

    with cloned_repo(bare_repo_url):
        pass

    assert captured_env.get("GIT_TERMINAL_PROMPT") == "0"


def test_cloned_repo_cleans_up_on_exception(bare_repo_url: str) -> None:
    """If the body of the with-block raises, the tempdir is still removed."""
    captured_path: Path | None = None
    with pytest.raises(ValueError, match="boom"):
        with cloned_repo(bare_repo_url) as repo_path:
            captured_path = repo_path
            raise ValueError("boom")

    assert captured_path is not None
    assert not captured_path.exists()


def test_cloned_repo_no_top_level_anthropic_import() -> None:
    """Loading repo_fetcher must not pull anthropic in at import time."""
    import importlib
    import sys

    # Force a fresh import to be safe.
    for mod_name in list(sys.modules):
        if mod_name == "byline.repo_fetcher":
            del sys.modules[mod_name]

    importlib.import_module("byline.repo_fetcher")
    # anthropic should not be loaded *because of* importing repo_fetcher.
    # Other tests may have already loaded it, so we instead inspect the module
    # source for an `import anthropic` line.
    src = Path(__file__).parent.parent / "byline" / "repo_fetcher.py"
    text = src.read_text()
    assert "import anthropic" not in text
    assert "from anthropic" not in text


def test_cloned_repo_does_not_leak_temp_dirs(bare_repo_url: str) -> None:
    """Confirm TemporaryDirectory cleanup against the system tempdir."""
    tmp_root = Path(tempfile.gettempdir())
    # Snapshot byline-related entries before/after; we only care that the
    # specific path we got handed back doesn't survive.
    with cloned_repo(bare_repo_url) as repo_path:
        assert (
            repo_path.is_relative_to(tmp_root)
            or str(repo_path).startswith(str(tmp_root))
            or str(repo_path).startswith("/tmp")
        )
        existing = repo_path
    assert not existing.exists()


def test_environment_isolation_between_tests() -> None:
    """Sanity: GITHUB_TOKEN modifications from other tests shouldn't leak here."""
    # This test exists just to confirm pytest's monkeypatch teardown is working.
    # It's not load-bearing for the spec; it just protects against future drift.
    assert os.environ.get("GITHUB_TOKEN") != "ghp_super_secret_token_value"
