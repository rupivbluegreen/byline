"""Local repo materialization for forensic analysis. See spec v0.2 §5.1.

Provides a context-managed clone helper so downstream stages (history walk,
deterministic alignment, etc.) can operate on a real filesystem checkout
without each having to manage its own tempdir lifecycle.

The clone preserves full history by default (``depth=None``) — earlier stages
of the comparative-attribution pipeline need this to walk commits, compute
self-baselines, and inspect formatting drift over time. Callers that don't
need history may pass an explicit ``depth`` to save bandwidth.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import git

logger = logging.getLogger(__name__)


def is_local_path(target: str) -> bool:
    """Return True if ``target`` looks like a local filesystem path.

    The classifier is intentionally lexical (no stat calls): the CLI uses this
    to decide whether to clone or to treat the input as an already-on-disk
    checkout, and we want that decision stable regardless of whether the path
    actually exists yet.

    Treated as local: absolute paths (``/...``), relative prefixes
    (``./``, ``../``, ``.``), user-home (``~``), and any string that
    contains no URL scheme separator and isn't an ssh-style ``git@host:repo``.

    Treated as remote: anything starting with ``http://``, ``https://``,
    ``git@``, ``ssh://``, or the bare ``github.com/...`` shorthand.
    """
    if not target:
        return False

    # Explicit local prefixes — fast path for the common cases.
    if target.startswith(("/", "./", "../", "~")):
        return True
    if target == ".":
        return True

    # Explicit remote prefixes.
    if target.startswith(("http://", "https://", "ssh://", "git@", "github.com/")):
        return False

    # Anything with a scheme separator we can't classify locally — call it remote.
    if "://" in target:
        return False

    # Fallback: no scheme, no ssh syntax, not the github.com shorthand → local.
    return True


def _build_clone_url(repo_url: str) -> str:
    """Return ``repo_url`` with ``GITHUB_TOKEN`` embedded for HTTPS URLs.

    Token comes from ``os.environ.get("GITHUB_TOKEN")``. Only ``https://``
    URLs get the rewrite — ``http://`` is rare and unsafe, ``file://`` and
    ``git@`` don't have a place to put the token. Returns the URL unchanged
    when no token is set.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return repo_url
    if not repo_url.startswith("https://"):
        return repo_url

    parts = urlsplit(repo_url)
    # If the URL already has a userinfo component, leave it alone — caller's
    # intent wins over our env-var convenience.
    if "@" in parts.netloc:
        return repo_url

    new_netloc = f"{token}@{parts.netloc}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


def _redact_url(url: str) -> str:
    """Strip any ``user[:pass]@`` userinfo from ``url`` for safe logging."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    # Keep just the host part; drop credentials entirely.
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


@contextmanager
def cloned_repo(repo_url: str, depth: int | None = None) -> Iterator[Path]:
    """Clone ``repo_url`` into a tempdir, yield the Path, clean up on exit.

    ``depth=None`` preserves full history (default) — required for the
    forensic stages that walk commits. Callers can pass an explicit positive
    integer to get a shallow clone.

    Behavior:
      * ``GIT_TERMINAL_PROMPT=0`` is set in the subprocess env so a clone that
        would otherwise prompt for credentials fails fast.
      * If ``GITHUB_TOKEN`` is set and ``repo_url`` is ``https://``, the token
        is embedded in the URL passed to git. The token is never written to
        log output.
      * Any clone failure is re-raised as a clean :class:`RuntimeError` so
        callers don't have to import gitpython to catch it.

    The yielded path is rooted under :func:`tempfile.gettempdir()` and is
    removed automatically when the context exits, including on exceptions.
    """
    effective_url = _build_clone_url(repo_url)
    redacted = _redact_url(effective_url)
    logger.info("cloning %s (depth=%s)", redacted, depth)

    clone_kwargs: dict[str, object] = {
        "env": {"GIT_TERMINAL_PROMPT": "0"},
    }
    if depth is not None:
        clone_kwargs["depth"] = depth

    with tempfile.TemporaryDirectory(prefix="byline-clone-") as tmpdir:
        dest = Path(tmpdir) / "repo"
        try:
            git.Repo.clone_from(effective_url, str(dest), **clone_kwargs)
        except git.GitCommandError as exc:
            # Re-raise as RuntimeError so callers don't depend on gitpython's
            # exception hierarchy. We deliberately don't include the original
            # URL in the message to avoid leaking a token if one was embedded
            # — gitpython's own messages already redact the URL.
            raise RuntimeError(f"git clone failed for {_redact_url(repo_url)}: {exc}") from exc
        except Exception as exc:  # pragma: no cover — defensive
            raise RuntimeError(f"git clone failed for {_redact_url(repo_url)}: {exc}") from exc

        yield dest


def detect_default_branch(repo_path: Path) -> str:
    """Return the current branch name of ``repo_path``, or ``"main"`` on fallback.

    Uses ``git.Repo.active_branch.name``. If the repo is in detached-HEAD
    state, isn't a git repo at all, or any other gitpython error surfaces, we
    return ``"main"`` — downstream stages can re-resolve via the remote if
    they need a different branch.
    """
    try:
        repo = git.Repo(str(repo_path))
        return repo.active_branch.name
    except Exception:
        # Detached HEAD raises TypeError; missing/invalid repo raises
        # InvalidGitRepositoryError or NoSuchPathError. Anything else also
        # falls back rather than crashing the analysis pipeline.
        return "main"
