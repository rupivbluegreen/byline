"""Tests for byline.github_client — GitHub REST client per spec §6."""

from __future__ import annotations

import base64
import time

import httpx
import pytest

from byline import github_client
from byline.github_client import (
    _request,
    get_file_content,
    get_recent_commit_messages,
    get_repo_tree,
    get_user_profile_readme,
    get_user_repos,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_headers(remaining: int = 5000, reset_offset: int = 3600) -> dict[str, str]:
    return {
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(time.time()) + reset_offset),
    }


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


def test_request_sets_bearer_when_token_provided():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        captured["accept"] = request.headers.get("Accept", "")
        captured["ua"] = request.headers.get("User-Agent", "")
        return httpx.Response(200, json={}, headers=_ok_headers())

    with _make_client(handler) as client:
        _request("GET", "https://api.github.com/foo", token="abc123", client=client)

    assert captured["auth"] == "Bearer abc123"
    assert "application/vnd.github+json" in captured["accept"]
    assert "byline" in captured["ua"]


def test_request_no_auth_header_when_no_token():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={}, headers=_ok_headers())

    with _make_client(handler) as client:
        _request("GET", "https://api.github.com/foo", token=None, client=client)

    assert captured["auth"] == ""


def test_request_sleeps_when_rate_limit_low(monkeypatch):
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(github_client.time, "sleep", fake_sleep)

    reset_at = int(time.time()) + 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={},
            headers={
                "X-RateLimit-Remaining": "3",
                "X-RateLimit-Reset": str(reset_at),
            },
        )

    with _make_client(handler) as client:
        _request("GET", "https://api.github.com/foo", token="t", client=client)

    assert len(slept) == 1
    # Should sleep at least until reset_at (with +1s buffer), so >= ~5s but <= ~6s
    assert slept[0] >= 0
    assert slept[0] <= 7


def test_request_raises_systemexit_on_403_no_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded for 1.2.3.4"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 60)},
        )

    with _make_client(handler) as client:
        with pytest.raises(SystemExit) as exc_info:
            _request("GET", "https://api.github.com/foo", token=None, client=client)
    assert exc_info.value.code == 2


def test_request_403_with_token_does_not_systemexit(monkeypatch):
    # A 403 with a token shouldn't exit — it should raise via raise_for_status.
    monkeypatch.setattr(github_client.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers=_ok_headers(remaining=0),
        )

    with _make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _request("GET", "https://api.github.com/foo", token="t", client=client)


def test_request_raises_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"}, headers=_ok_headers())

    with _make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _request("GET", "https://api.github.com/foo", token="t", client=client)


# ---------------------------------------------------------------------------
# get_user_repos
# ---------------------------------------------------------------------------


def test_get_user_repos_filters_forks_and_archived(monkeypatch):
    repos = [
        {"name": "good1", "fork": False, "archived": False, "size": 100, "stargazers_count": 10},
        {"name": "fork", "fork": True, "archived": False, "size": 100, "stargazers_count": 50},
        {"name": "archived", "fork": False, "archived": True, "size": 100, "stargazers_count": 30},
        {"name": "empty", "fork": False, "archived": False, "size": 0, "stargazers_count": 5},
        {"name": "good2", "fork": False, "archived": False, "size": 50, "stargazers_count": 20},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repos, headers=_ok_headers())

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_user_repos("someone", token="t")

    names = [r["name"] for r in result]
    assert "fork" not in names
    assert "archived" not in names
    assert "empty" not in names
    # Sorted by stargazers desc: good2 (20), good1 (10)
    assert names == ["good2", "good1"]


def test_get_user_repos_caps_at_30(monkeypatch):
    repos = [
        {
            "name": f"repo{i}",
            "fork": False,
            "archived": False,
            "size": 100,
            "stargazers_count": i,
        }
        for i in range(50)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repos, headers=_ok_headers())

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_user_repos("someone", token="t")
    assert len(result) == 30
    # Should be top-starred first
    assert result[0]["stargazers_count"] == 49


# ---------------------------------------------------------------------------
# get_repo_tree
# ---------------------------------------------------------------------------


def test_get_repo_tree_skips_lockfiles_and_binaries(monkeypatch):
    repo_meta = {"default_branch": "main"}
    tree_payload = {
        "tree": [
            {"path": "README.md", "type": "blob", "size": 100, "sha": "a"},
            {"path": "src/main.py", "type": "blob", "size": 200, "sha": "b"},
            {"path": "package-lock.json", "type": "blob", "size": 9999, "sha": "c"},
            {"path": "poetry.lock", "type": "blob", "size": 1000, "sha": "d"},
            {"path": "assets/logo.png", "type": "blob", "size": 5000, "sha": "e"},
            {"path": "bin/tool.exe", "type": "blob", "size": 6000, "sha": "f"},
            {"path": "node_modules/foo/index.js", "type": "blob", "size": 100, "sha": "g"},
            {"path": "src", "type": "tree", "size": 0, "sha": "h"},
            {"path": "dist/output.js", "type": "blob", "size": 100, "sha": "i"},
            {"path": "vendor/foo.go", "type": "blob", "size": 100, "sha": "j"},
            {"path": "__pycache__/foo.pyc", "type": "blob", "size": 100, "sha": "k"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/o/r"):
            return httpx.Response(200, json=repo_meta, headers=_ok_headers())
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json=tree_payload, headers=_ok_headers())
        return httpx.Response(404, json={}, headers=_ok_headers())

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_repo_tree("o", "r", token="t")
    paths = [e["path"] for e in result]
    assert "README.md" in paths
    assert "src/main.py" in paths
    assert "package-lock.json" not in paths
    assert "poetry.lock" not in paths
    assert "assets/logo.png" not in paths
    assert "bin/tool.exe" not in paths
    assert "node_modules/foo/index.js" not in paths
    assert "dist/output.js" not in paths
    assert "vendor/foo.go" not in paths
    assert "__pycache__/foo.pyc" not in paths
    # No tree-type entries
    assert all(e["type"] == "blob" for e in result)


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------


def test_get_file_content_decodes_base64(monkeypatch):
    raw = "hello world\nthis is content"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": encoded},
            headers=_ok_headers(),
        )

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_file_content("o", "r", "README.md", token="t")
    assert result == raw


def test_get_file_content_returns_empty_for_oversize(monkeypatch):
    big = b"x" * 600_000
    encoded = base64.b64encode(big).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": encoded},
            headers=_ok_headers(),
        )

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_file_content("o", "r", "big.txt", token="t")
    assert result == ""


# ---------------------------------------------------------------------------
# get_recent_commit_messages
# ---------------------------------------------------------------------------


def test_get_recent_commit_messages_skips_merges(monkeypatch):
    commits = [
        {"commit": {"message": "feat: add thing\n\nbody"}, "parents": [{"sha": "p1"}]},
        {
            "commit": {"message": "Merge pull request #1\n\nbody"},
            "parents": [{"sha": "p1"}, {"sha": "p2"}],
        },
        {"commit": {"message": "fix: bug"}, "parents": [{"sha": "p1"}]},
        {"commit": {"message": "chore: deps"}, "parents": []},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=commits, headers=_ok_headers())

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_recent_commit_messages("o", "r", token="t", limit=10)
    assert result == ["feat: add thing", "fix: bug", "chore: deps"]


# ---------------------------------------------------------------------------
# get_user_profile_readme
# ---------------------------------------------------------------------------


def test_get_user_profile_readme_returns_none_on_404(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"}, headers=_ok_headers())

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_user_profile_readme("someuser", token="t")
    assert result is None


def test_get_user_profile_readme_returns_text_when_present(monkeypatch):
    raw = "# Hi there"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": encoded},
            headers=_ok_headers(),
        )

    client = _make_client(handler)
    monkeypatch.setattr(github_client, "_make_default_client", lambda: client)

    result = get_user_profile_readme("someuser", token="t")
    assert result == raw
