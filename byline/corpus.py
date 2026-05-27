"""Baseline corpus assembly from a candidate's public history. See spec §8.

The corpus builder walks a candidate's public GitHub footprint and collects
:class:`~byline.models.WritingSample` entries that downstream comparative
analysis can lean on:

* READMEs from up to 30 of the candidate's non-fork, non-archived repos.
* Recent commit-message bodies from those same repos (joined into one sample).
* The candidate's profile README (``<username>/<username>``), if present.

Per-repo failures are recorded in ``repos_skipped`` and never abort the build.
Word counting uses whitespace-split, consistent with :mod:`byline.metrics`.
"""

from __future__ import annotations

import logging

import httpx

from byline.github_client import (
    get_file_content,
    get_recent_commit_messages,
    get_user_profile_readme,
    get_user_repos,
)
from byline.models import BaselineCorpus, WritingSample

logger = logging.getLogger(__name__)

# Minimum word counts for a piece of writing to be worth keeping. Below these
# floors the signal is too sparse to support comparative analysis and gets
# dropped silently.
_MIN_README_WORDS = 50
_MIN_COMMITS_WORDS = 30
_MIN_PROFILE_README_WORDS = 30


def _word_count(text: str) -> int:
    """Whitespace-split token count, matching the convention used in metrics.py."""
    return len(text.split())


def build_corpus(
    username: str,
    token: str | None,
    max_repos: int = 30,
) -> BaselineCorpus:
    """Build a :class:`BaselineCorpus` from the candidate's public GitHub footprint.

    Pulls READMEs and recent commit messages from up to ``max_repos`` of the
    candidate's non-fork, non-archived repositories (already capped and
    star-sorted by ``get_user_repos``), plus the user's profile README when
    available. Per-repo HTTP failures are recorded in ``repos_skipped`` and
    never abort the build.
    """
    repos = get_user_repos(username, token)
    if max_repos is not None:
        repos = repos[:max_repos]

    samples: list[WritingSample] = []
    repos_skipped: list[tuple[str, str]] = []
    repos_scanned = [r["full_name"] for r in repos]

    for repo_meta in repos:
        full_name = repo_meta.get("full_name", "")
        owner = (repo_meta.get("owner") or {}).get("login", "")
        name = repo_meta.get("name", "")
        if not owner or not name:
            repos_skipped.append((full_name or name or "<unknown>", "missing owner/name"))
            continue

        try:
            readme_text = get_file_content(owner, name, "README.md", token)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            reason = f"README fetch failed: HTTP {status}"
            logger.warning("skipping %s — %s", full_name, reason)
            repos_skipped.append((full_name, reason))
            continue
        except httpx.HTTPError as exc:
            reason = f"README fetch failed: {type(exc).__name__}"
            logger.warning("skipping %s — %s", full_name, reason)
            repos_skipped.append((full_name, reason))
            continue

        if readme_text:
            words = _word_count(readme_text)
            if words > _MIN_README_WORDS:
                samples.append(
                    WritingSample(
                        source=f"README.md@{full_name}",
                        kind="readme",
                        text=readme_text,
                        word_count=words,
                    )
                )
                logger.info("added README sample from %s (%d words)", full_name, words)

        try:
            messages = get_recent_commit_messages(owner, name, token, limit=20)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            reason = f"commits fetch failed: HTTP {status}"
            logger.warning("skipping commits for %s — %s", full_name, reason)
            repos_skipped.append((full_name, reason))
            continue
        except httpx.HTTPError as exc:
            reason = f"commits fetch failed: {type(exc).__name__}"
            logger.warning("skipping commits for %s — %s", full_name, reason)
            repos_skipped.append((full_name, reason))
            continue

        joined = "\n".join(m for m in messages if m and m.strip())
        if joined:
            words = _word_count(joined)
            if words > _MIN_COMMITS_WORDS:
                samples.append(
                    WritingSample(
                        source=f"commit_messages@{full_name}",
                        kind="commit_message",
                        text=joined,
                        word_count=words,
                    )
                )
                logger.info("added commit-message sample from %s (%d words)", full_name, words)

    # Profile README is a separate top-level source — failure here is logged
    # but doesn't abort the build.
    try:
        profile_text = get_user_profile_readme(username, token)
    except httpx.HTTPError as exc:
        logger.warning("profile README fetch failed for %s: %s", username, exc)
        profile_text = None

    if profile_text:
        words = _word_count(profile_text)
        if words > _MIN_PROFILE_README_WORDS:
            samples.append(
                WritingSample(
                    source=f"README.md@{username}/{username}",
                    kind="readme",
                    text=profile_text,
                    word_count=words,
                )
            )
            logger.info("added profile README sample for %s (%d words)", username, words)

    total_words = sum(s.word_count for s in samples)

    return BaselineCorpus(
        username=username,
        samples=samples,
        total_words=total_words,
        repos_scanned=repos_scanned,
        repos_skipped=repos_skipped,
    )
