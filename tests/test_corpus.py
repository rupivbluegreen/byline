"""Tests for byline.corpus — baseline corpus builder per spec §8."""

from __future__ import annotations

import httpx

from byline import corpus
from byline.models import BaselineCorpus, WritingSample

# ---------------------------------------------------------------------------
# Fakes / fixture helpers
# ---------------------------------------------------------------------------


def _repo(owner: str, name: str) -> dict:
    return {
        "full_name": f"{owner}/{name}",
        "name": name,
        "owner": {"login": owner},
    }


# A README of clearly > 50 words.
_LONG_README = (
    "This project is a small but earnest tool for evaluating writing samples "
    "across a candidate's public history. It walks repositories, gathers prose, "
    "and produces a baseline that downstream comparative checks can lean on. "
    "We document the approach in plain language so that anyone can follow the "
    "shape of the analysis from start to finish without ambiguity at all today."
)

# A short README intentionally under 50 words to exercise the gate.
_SHORT_README = "Hello world. A tiny placeholder readme with not many words at all."


def _fake_get_user_repos(_username: str, _token: str | None):
    return [_repo("alice", "foo"), _repo("alice", "bar")]


def _fake_get_file_content(owner: str, repo: str, path: str, token: str | None):
    assert path == "README.md"
    if owner == "alice" and repo == "foo":
        return _LONG_README
    if owner == "alice" and repo == "bar":
        return _LONG_README + " Additional sentence with more comparative-signal content for bar."
    if owner == "alice" and repo == "alice":
        return (
            "Hello, I'm Alice. I write software and prose about software. "
            "This profile readme has more than thirty whitespace tokens by intent "
            "so the corpus builder will keep it as a useful baseline sample today."
        )
    return ""


def _fake_get_recent_commit_messages(owner: str, repo: str, token: str | None, limit: int = 20):
    return [
        f"feat: add thing in {repo}",
        f"fix: correct edge case in {repo} parser when input is empty or partial",
        f"docs: explain motivation behind {repo} comparative-signal pipeline at length",
        f"refactor: tidy up {repo} module to make later changes easier and clearer",
    ]


def _fake_get_user_profile_readme(username: str, token: str | None):
    return (
        "Hello, I'm Alice. I write software and prose about software. "
        "This profile readme has more than thirty whitespace tokens by intent "
        "so the corpus builder will keep it as a useful baseline sample today."
    )


def _install_fakes(
    monkeypatch, *, file_content=None, profile_readme=None, repos=None, commits=None
):
    monkeypatch.setattr(corpus, "get_user_repos", repos or _fake_get_user_repos)
    monkeypatch.setattr(corpus, "get_file_content", file_content or _fake_get_file_content)
    monkeypatch.setattr(
        corpus,
        "get_recent_commit_messages",
        commits or _fake_get_recent_commit_messages,
    )
    monkeypatch.setattr(
        corpus,
        "get_user_profile_readme",
        profile_readme or _fake_get_user_profile_readme,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_corpus_happy_path(monkeypatch):
    _install_fakes(monkeypatch)

    result = corpus.build_corpus("alice", token=None)

    assert isinstance(result, BaselineCorpus)
    assert result.username == "alice"
    assert result.repos_scanned == ["alice/foo", "alice/bar"]
    assert result.repos_skipped == []

    kinds = {s.kind for s in result.samples}
    assert "readme" in kinds
    assert "commit_message" in kinds

    sources = [s.source for s in result.samples]
    assert "README.md@alice/foo" in sources
    assert "README.md@alice/bar" in sources
    assert "commit_messages@alice/foo" in sources
    assert "commit_messages@alice/bar" in sources
    # Profile readme is included as kind=readme with the profile path.
    assert "README.md@alice/alice" in sources

    assert result.total_words == sum(s.word_count for s in result.samples)
    assert result.total_words > 0

    # Every sample carries non-empty text and a matching word count.
    for sample in result.samples:
        assert isinstance(sample, WritingSample)
        assert sample.text.strip()
        assert sample.word_count == len(sample.text.split())


# ---------------------------------------------------------------------------
# Per-repo errors don't fail the whole build
# ---------------------------------------------------------------------------


def test_build_corpus_skips_repo_on_http_error(monkeypatch):
    def file_content(owner: str, repo: str, path: str, token: str | None):
        if repo == "bar":
            request = httpx.Request(
                "GET", "https://api.github.com/repos/alice/bar/contents/README.md"
            )
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return _fake_get_file_content(owner, repo, path, token)

    _install_fakes(monkeypatch, file_content=file_content)

    result = corpus.build_corpus("alice", token=None)

    assert isinstance(result, BaselineCorpus)
    skipped_names = [name for name, _reason in result.repos_skipped]
    assert "alice/bar" in skipped_names

    # foo's readme still made it in.
    sources = [s.source for s in result.samples]
    assert "README.md@alice/foo" in sources
    assert "README.md@alice/bar" not in sources


# ---------------------------------------------------------------------------
# Profile readme None -> no sample, no crash
# ---------------------------------------------------------------------------


def test_build_corpus_profile_readme_none(monkeypatch):
    _install_fakes(monkeypatch, profile_readme=lambda username, token: None)

    result = corpus.build_corpus("alice", token=None)

    sources = [s.source for s in result.samples]
    assert "README.md@alice/alice" not in sources
    # But the build still succeeded with other samples.
    assert any(s.source.startswith("README.md@alice/") for s in result.samples)


# ---------------------------------------------------------------------------
# Word-count gate: README < 50 words is dropped
# ---------------------------------------------------------------------------


def test_build_corpus_drops_short_readme(monkeypatch):
    def file_content(owner: str, repo: str, path: str, token: str | None):
        if repo == "foo":
            return _SHORT_README
        return _fake_get_file_content(owner, repo, path, token)

    _install_fakes(monkeypatch, file_content=file_content)

    result = corpus.build_corpus("alice", token=None)

    sources = [s.source for s in result.samples]
    assert "README.md@alice/foo" not in sources
    # bar's long readme should still be there.
    assert "README.md@alice/bar" in sources
