# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/rupivbluegreen/byline/releases/tag/v0.1.0
