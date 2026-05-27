# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-05-27

### Added

- Multi-provider LLM support via `byline/llm_provider.py`. New
  `BYLINE_LLM_PROVIDER` env var selects between `anthropic` (default,
  unchanged) and `openai`. The OpenAI provider also covers any
  OpenAI-compatible self-hosted endpoint (Ollama, vLLM, LM Studio,
  llama.cpp) via `OPENAI_BASE_URL`.
- `openai>=1.0` added under the `[llm]` extra alongside `anthropic`.
  Both SDKs remain lazy-imported; the base install still pulls neither.
- New `docs/llm-providers.md` documents the three setup paths
  (Anthropic, OpenAI, self-hosted) in one place, including
  troubleshooting and the env-var contract.

### Changed

- `byline.llm` now accepts an `LLMProvider` parameter where it
  previously took a raw `anthropic.Anthropic` client. The original
  `anthropic_client` parameter and `get_anthropic_client()` helper are
  preserved as backwards-compat aliases that delegate to the new
  `provider` parameter and `get_llm_provider()` factory.
- Banned-phrase scrubbing (`strip_banned_phrases`) now applies
  uniformly to every provider's output, not just Claude's. The
  framing-rule enforcement is provider-agnostic.
- CLI error hint when no API key is set now mentions both
  `BYLINE_LLM_PROVIDER` and `OPENAI_BASE_URL` so reviewers can find
  the self-hosted path from the failure message.
- CI `test-base-install` job tightened: now asserts that neither
  `anthropic` nor `openai` is imported transitively at base-install
  time, and runs the full deterministic test list without the
  previous `|| true` escape hatch.

## [0.2.0] - 2026-05-27

### Added

- Repo fetcher helper (`byline/repo_fetcher.py`): context-managed
  clone helper that materialises remote GitHub URLs into a real
  filesystem checkout so the history, alignment, and self-baseline
  passes can run uniformly across local paths and remote inputs.
- Commit history forensics module (`byline/history.py`): timeline
  burst detection, commit-message style profiling, author identity drift
  detection, file-evolution paste detection.
- Documentation-implementation alignment module (`byline/alignment.py`)
  with a deterministic mode (CLI flags, env vars, commands, dependencies)
  and an optional LLM-powered semantic mode under `[llm]` extra.
- Within-repo self-baseline (`byline/self_baseline.py`) comparing
  commit messages, README, and code comments.
- First-person voice and AI-disclosure detection (`byline/voice.py`).
  Disclosure is rendered as a positive trust signal and shifts the
  overall signal toward `aligned`.
- Boilerplate meta-file density check (`byline/boilerplate.py`).
- New CLI subcommands:
  - `byline questions` — interview question generator (LLM required).
  - `byline chat` — interactive REPL session over the audit (LLM required).
  - `byline align` — standalone alignment check (LLM optional).
- New `--no-history` flag on `audit` to skip commit forensics.
- `gitpython>=3.1` and `prompt_toolkit>=3.0` runtime dependencies.
- CI `test-base-install` job verifying the tool works without the
  `[llm]` extra installed.
- Released to PyPI as `byline-audit` (install with `pip install byline-audit`).

### Changed

- `overall_signal()` heuristic extended to incorporate history,
  alignment, voice, boilerplate, and self-baseline findings.
- `AuditResult` model gains optional `history`, `alignment`, `voice`,
  `boilerplate`, and `self_baseline` fields.
- All `anthropic` imports are deferred to inside the functions that
  need them, so the base install never pays the import cost.
- LLM outputs are post-processed to strip any banned phrases
  ("AI detector", "detect AI", "the candidate used AI") before
  reaching the user.

## [0.1.0] - 2026-05-27

### Added

- Typer-based CLI exposing three commands:
  - `audit` — analyze a single GitHub submission and emit a comparative report.
  - `baseline` — build a per-author baseline corpus from public GitHub history.
  - `scan` — batch-process a directory of submissions.
- Style and readability metrics module (sentence length variance, lexical
  density, Flesch reading ease, em-dash usage, header-emoji density, list
  shape statistics, banner block detection).
- Fingerprint module that scans text and code for catalogued AI-associated
  phrases and section headers, returning per-match weights and context.
- Disproportion analysis that compares prose-to-code ratios, comment density,
  and documentation depth against typical baselines.
- Comparative scoring that combines metric divergence, fingerprint density,
  and disproportion indicators into a single signal score with per-component
  breakdown.
- Markdown report renderer producing a structured, reviewer-facing summary.
- DOCX report renderer for distribution to non-technical stakeholders.
- Optional Anthropic-powered qualitative pass under the `[llm]` extra that
  adds a narrative commentary section to reports.
- Pydantic v2 data models covering submissions, baselines, metrics, matches,
  and report payloads.
- Apache 2.0 license, NOTICE, and methodology stub.
- GitHub Actions CI matrix on Python 3.10 / 3.11 / 3.12 running ruff and
  pytest with coverage.

### Framing

This release establishes the project's framing rule: `byline` produces
**signals of divergence from a baseline**, never authorship verdicts. All
output language and documentation reflects this.

[0.3.0]: https://github.com/rupivbluegreen/byline/releases/tag/v0.3.0
[0.2.0]: https://github.com/rupivbluegreen/byline/releases/tag/v0.2.0
[0.1.0]: https://github.com/rupivbluegreen/byline/releases/tag/v0.1.0
