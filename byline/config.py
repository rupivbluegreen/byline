"""Configuration loading and runtime settings. See spec §13.

This module exposes the small set of helpers the CLI relies on to surface
credentials and tune logging. Token resolution favours an explicit ``--``
flag, falling back to the corresponding environment variable so the help text
stays unsurprising::

    byline audit ... --github-token gh_xxx        # explicit
    GITHUB_TOKEN=gh_xxx byline audit ...          # implicit via env

The optional Anthropic key has no CLI flag — it's read from the environment
only — because secrets passed on the shell are easy to leak via shell
history. ``configure_logging`` centralises the basic ``logging`` setup so
every command honours ``-v`` / ``-vv`` identically.
"""

from __future__ import annotations

import logging
import os
from typing import Final

#: Logging format applied by :func:`configure_logging`. Kept terse so multiple
#: messages stay readable on a single terminal width.
DEFAULT_LOG_FORMAT: Final = "%(levelname)s %(name)s: %(message)s"


def resolve_github_token(cli_arg: str | None) -> str | None:
    """Return the GitHub PAT to use, preferring the CLI flag over the env var.

    Falls back to the ``GITHUB_TOKEN`` environment variable when ``cli_arg`` is
    ``None`` or an empty string. Returns ``None`` when neither source supplies
    a value — callers may then either run anonymously (subject to GitHub's
    tight unauthenticated rate limits) or fail with a clear message.
    """

    if cli_arg:
        return cli_arg
    return os.environ.get("GITHUB_TOKEN")


def resolve_anthropic_key() -> str | None:
    """Return the ``ANTHROPIC_API_KEY`` env var, or ``None`` if it's unset."""

    return os.environ.get("ANTHROPIC_API_KEY")


def configure_logging(verbose: int) -> None:
    """Configure stdlib logging from a repeatable ``-v`` count.

    Mapping:

    * ``verbose == 0`` -> ``WARNING`` (default; quiet)
    * ``verbose == 1`` -> ``INFO``
    * ``verbose >= 2`` -> ``DEBUG``

    Uses ``force=True`` so a re-invocation (for example, when tests call into
    the CLI more than once in the same process) replaces any handlers a prior
    call installed instead of stacking new ones.
    """

    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format=DEFAULT_LOG_FORMAT, force=True)
