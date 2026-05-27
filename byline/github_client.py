"""Minimal httpx-based GitHub REST client used by audit and baseline. See spec §6.

Synchronous client; uses httpx directly (no PyGithub). Handles GitHub rate-limit
headers cooperatively (sleeps until reset when remaining is low) and converts the
"no token + 403 rate-limited" condition into a clean SystemExit(2) with guidance.

All helpers operate against the public REST API at ``https://api.github.com``.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 30.0

_USER_AGENT = "byline/0.1.0"

# Filenames (basename match) to skip when walking a repo tree — typically lockfiles
# whose content adds no signal value and inflates token/byte budgets.
_SKIP_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
        "pnpm-lock.yaml",
        "composer.lock",
        "Gemfile.lock",
    }
)

# File extensions to skip — binary blobs and compiled artifacts.
_SKIP_EXTENSIONS: tuple[str, ...] = (
    ".bin",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".ico",
    ".mp4",
    ".mov",
    ".webp",
    ".wasm",
    ".class",
    ".pyc",
)

# Directory prefixes to skip — vendored deps and build output.
_SKIP_DIR_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    "vendor/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "build/",
)

# Files larger than this (after base64 decode) are returned as empty by
# get_file_content — for v0.1 we treat them as noise rather than signal.
_MAX_FILE_BYTES = 500_000

# get_user_repos paginates conservatively to keep the call budget tight.
_MAX_REPO_PAGES = 3
_MAX_REPOS_RETURNED = 30


def _make_default_client() -> httpx.Client:
    """Construct the default httpx.Client used when callers don't inject one.

    Tests monkeypatch this to return a Client backed by ``httpx.MockTransport``.
    """
    return httpx.Client(timeout=DEFAULT_TIMEOUT)


def _request(
    method: str,
    url: str,
    token: str | None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """Perform a single GitHub REST request with auth, UA, and rate-limit handling.

    Behavior:
      * Adds ``Accept: application/vnd.github+json`` and ``User-Agent: byline/0.1.0``.
      * Adds ``Authorization: Bearer <token>`` when ``token`` is provided.
      * After the response, inspects rate-limit headers. If
        ``X-RateLimit-Remaining < 10``, sleeps until ``X-RateLimit-Reset`` + 1s
        before returning so subsequent calls don't blow through the quota.
      * If the response is a 403 *and* no token was supplied, raises
        ``SystemExit(2)`` instructing the operator to set ``GITHUB_TOKEN``.
      * Otherwise calls ``raise_for_status`` so 4xx/5xx propagate as
        ``httpx.HTTPStatusError`` (callers may catch 404s where meaningful).

    The ``client`` parameter is for test injection (e.g. ``MockTransport``);
    public callers pass ``None`` and a fresh client is created and closed.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    if client is None:
        client = _make_default_client()

    try:
        response = client.request(method, url, headers=headers, params=params, timeout=timeout)
    finally:
        if owns_client:
            client.close()

    remaining_header = response.headers.get("X-RateLimit-Remaining")
    reset_header = response.headers.get("X-RateLimit-Reset")
    logger.debug(
        "github request %s %s -> %s (rl-remaining=%s)",
        method,
        url,
        response.status_code,
        remaining_header,
    )

    # Cooperative back-off when nearing the rate-limit ceiling. We do this even
    # when the response succeeded so that the *next* call doesn't 403.
    try:
        remaining = int(remaining_header) if remaining_header is not None else None
    except ValueError:
        remaining = None
    try:
        reset_at = int(reset_header) if reset_header is not None else None
    except ValueError:
        reset_at = None

    # 403 + no token typically means anonymous quota exhausted. Exit cleanly
    # with the spec's exit code rather than dumping a traceback or sleeping for
    # an hour against an unreachable reset window.
    if response.status_code == 403 and not token:
        logger.error(
            "GitHub returned 403 (likely anonymous rate limit). "
            "Set GITHUB_TOKEN in the environment and retry."
        )
        raise SystemExit(2)

    if remaining is not None and remaining < 10:
        wait = 0
        if reset_at is not None:
            wait = max(0, reset_at - int(time.time())) + 1
        logger.warning(
            "github rate-limit low (remaining=%s); sleeping %ss until reset",
            remaining,
            wait,
        )
        time.sleep(wait)

    response.raise_for_status()
    return response


def _next_page_url(response: httpx.Response) -> str | None:
    """Parse the ``Link`` header for the ``rel="next"`` URL, if any."""
    link = response.headers.get("Link")
    if not link:
        return None
    for part in link.split(","):
        segments = part.strip().split(";")
        if len(segments) < 2:
            continue
        url_part = segments[0].strip()
        rel_part = ";".join(segments[1:]).strip()
        if rel_part == 'rel="next"' and url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None


def get_user_repos(username: str, token: str | None) -> list[dict]:
    """Return up to 30 non-fork, non-archived repos for a user, sorted by stars desc.

    Paginates the ``/users/{username}/repos`` endpoint via the ``Link`` header
    up to 3 pages. As a v0.1 approximation, repos with ``size == 0`` are dropped
    as a cheap stand-in for "has at least 2 commits" — peeking per-repo commit
    counts would cost an extra request per candidate.
    """
    url: str | None = f"{GITHUB_API}/users/{username}/repos?per_page=100&sort=updated"
    collected: list[dict] = []
    client = _make_default_client()
    try:
        pages = 0
        while url and pages < _MAX_REPO_PAGES:
            response = _request("GET", url, token=token, client=client)
            page = response.json()
            if not isinstance(page, list):
                break
            collected.extend(page)
            pages += 1
            url = _next_page_url(response)
    finally:
        client.close()

    filtered = [
        r
        for r in collected
        if not r.get("fork", False) and not r.get("archived", False) and r.get("size", 0) > 0
    ]
    filtered.sort(key=lambda r: r.get("stargazers_count", 0), reverse=True)
    return filtered[:_MAX_REPOS_RETURNED]


def _should_skip_tree_path(path: str) -> bool:
    """Return True if a tree path should be excluded from the corpus walk."""
    if any(path.startswith(prefix) for prefix in _SKIP_DIR_PREFIXES):
        return True
    basename = path.rsplit("/", 1)[-1]
    if basename in _SKIP_BASENAMES:
        return True
    lower = path.lower()
    if lower.endswith(_SKIP_EXTENSIONS):
        return True
    return False


def get_repo_tree(owner: str, repo: str, token: str | None) -> list[dict]:
    """Return blob entries from a repo's default-branch tree, with binaries skipped.

    Resolves the default branch via ``/repos/{owner}/{repo}``, then walks the
    recursive tree once. Only ``type == "blob"`` entries are kept; lockfiles,
    common binary extensions, and well-known vendored/build directories are
    filtered out. Each entry preserves ``path``, ``type``, ``size``, ``sha``.
    """
    client = _make_default_client()
    try:
        meta = _request(
            "GET", f"{GITHUB_API}/repos/{owner}/{repo}", token=token, client=client
        ).json()
        default_branch = meta.get("default_branch") or "main"
        tree_resp = _request(
            "GET",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{default_branch}",
            token=token,
            params={"recursive": "1"},
            client=client,
        ).json()
    finally:
        client.close()

    entries = tree_resp.get("tree", [])
    out: list[dict] = []
    for entry in entries:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        if _should_skip_tree_path(path):
            continue
        out.append(
            {
                "path": path,
                "type": entry.get("type"),
                "size": entry.get("size", 0),
                "sha": entry.get("sha"),
            }
        )
    return out


def get_file_content(owner: str, repo: str, path: str, token: str | None) -> str:
    """Fetch a file via the contents API and return decoded UTF-8 text.

    Returns the empty string for files larger than 500_000 bytes after decode
    (treated as non-signal noise in v0.1). UTF-8 decode uses ``errors="replace"``
    so stray bytes don't crash callers.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    response = _request("GET", url, token=token)
    payload = response.json()
    encoding = payload.get("encoding")
    raw_content = payload.get("content", "") or ""

    if encoding == "base64":
        try:
            decoded_bytes = base64.b64decode(raw_content)
        except (ValueError, base64.binascii.Error):
            return ""
    else:
        decoded_bytes = raw_content.encode("utf-8", errors="replace")

    if len(decoded_bytes) > _MAX_FILE_BYTES:
        return ""

    return decoded_bytes.decode("utf-8", errors="replace")


def get_recent_commit_messages(
    owner: str,
    repo: str,
    token: str | None,
    limit: int = 50,
) -> list[str]:
    """Return the first line of each non-merge commit, up to ``limit`` total.

    Commits with more than one parent (merge commits) are skipped since their
    messages are typically auto-generated and add no authorial signal.
    """
    messages: list[str] = []
    per_page = min(limit, 100)
    url: str | None = f"{GITHUB_API}/repos/{owner}/{repo}/commits?per_page={per_page}"
    client = _make_default_client()
    try:
        while url and len(messages) < limit:
            response = _request("GET", url, token=token, client=client)
            commits = response.json()
            if not isinstance(commits, list):
                break
            for commit_entry in commits:
                parents = commit_entry.get("parents", []) or []
                if len(parents) > 1:
                    continue
                commit_obj = commit_entry.get("commit", {}) or {}
                message = commit_obj.get("message", "") or ""
                first_line = message.split("\n", 1)[0].strip()
                if first_line:
                    messages.append(first_line)
                if len(messages) >= limit:
                    break
            if len(messages) >= limit:
                break
            url = _next_page_url(response) if limit > 100 else None
    finally:
        client.close()

    return messages


def get_user_profile_readme(username: str, token: str | None) -> str | None:
    """Return the contents of a user's ``<username>/<username>`` profile README.

    Returns None if no profile README repo exists (404). Other errors propagate.
    """
    try:
        return get_file_content(username, username, "README.md", token=token)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
