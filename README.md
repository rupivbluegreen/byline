# byline

`byline` is a comparative attribution toolkit for hiring reviewers. It compares the writing surface of a take-home submission (its READMEs, comments, and shell scripts) against the same candidate's prior public GitHub writing, and reports where the two diverge. Output is framed as a set of stylistic signals for human review, not as a judgement about who wrote the code.

![PyPI](https://img.shields.io/pypi/v/byline.svg)
![Python](https://img.shields.io/pypi/pyversions/byline.svg)
![CI](https://github.com/rupivblueground/byline/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)

## What this is, what this isn't

`byline` measures eight stylistic signals on a candidate's submission (em-dash density, emoji-in-headers ratio, sentence length, lexical diversity, typo rate, vocabulary sophistication, banner-comment density, and progress-UX scaffolding), compares each against the candidate's own GitHub baseline, and surfaces the deltas. It also runs a catalogue of phrasing and structural fingerprints against the prose, plus disproportion checks on doc-to-code ratio, diagram inventory, numbered-diagram patterns, and comment density. The intent is to give a reviewer one extra structured data point when deciding whether a submission warrants a follow-up conversation.

`byline` is not an "AI detector". Nothing in the output asserts authorship, and the framing is comparative attribution analysis rather than detection. The tool is built around the assumption that humans and machines both write prose, and the only signal worth surfacing is whether the submission diverges sharply from the candidate's own observable writing history. Treat every report as one input among many, alongside the interview, code review, and reference checks.

## Install

```bash
pip install byline
```

The optional `[llm]` extra pulls in the Anthropic SDK for the qualitative-pass feature:

```bash
pip install "byline[llm]"
```

## Quickstart

Three CLI commands cover the common workflows:

```bash
# Just the target (fingerprints + disproportion analysis)
byline scan ./candidate-submission

# Build the baseline alone
byline baseline candidate-username

# Full comparative audit
byline audit ./candidate-submission --candidate candidate-username
```

A `byline scan` run produces a short Markdown summary. The shape (illustrative, abbreviated):

```markdown
## Overall signal

**Overall signal: mixed.** Some metrics diverge from baseline while others
align; treat as a soft signal worth a closer look.

## Fingerprint findings

### README.md

- phrase / <pattern>: "<excerpt around the matched phrase>"
- structure / emoji_header_cluster: 6 emoji-prefixed headers
- phrase / <pattern>: "<excerpt around the matched phrase>"
```

Run `byline --help` (or `byline <command> --help`) for the full flag list.

## Limitations

> This report presents stylistic signals comparing a candidate's submission to their own observable writing baseline. It is one input into a hiring decision, never a determination of authorship, and must not be treated as evidence of misconduct. False positives are possible — non-native English writers, proofread submissions, tutorial-derived code, and team-authored repos can all produce divergent signals.

**Gameability.** A candidate who knows `byline` is part of the process can adjust their style: rewrite the README in their own voice, strip emoji headers, drop the banner-comment scaffolding from shell scripts. The tool is most useful when the comparison is run silently, and when the reviewer treats a clean report as no signal rather than positive evidence.

**False positives.** Several plausible candidate profiles can produce divergent signals without any underlying authorship problem. Non-native English writers may show shifted typo rates and sentence-length distributions versus a baseline collected from native-language prose. Candidates who proofread their submission heavily can look stylistically different from their casual GitHub commits. Tutorial-derived code carries the tutorial author's voice. Team-authored repositories blend multiple writers. Candidates with sparse public GitHub history have small baselines, and small baselines produce noisy deltas; the report flags this case.

**Scope.** `byline` v0.1 is English-only and GitHub-only. It reads prose and shell scripts; it does not analyse the code itself for AI patterns, and it does not attempt to identify which model (if any) generated a passage. Those questions are out of scope for this release.

## Ethical use

Disclose to candidates that this analysis is part of your review process before they submit. Use the report as one input alongside the interview, code walkthrough, and reference checks, never as the sole basis for a decision. Do not share the report with the candidate as an accusation; if a follow-up conversation is warranted, ask open questions about how the submission was put together and let the candidate explain. Reviewer judgement matters more than the tool's output. When in doubt, weight the human signal.

## How it works

The audit pipeline computes a `StyleProfile` for the candidate's baseline corpus and a matching profile for the submission, then emits a per-metric `ComparativeDelta` with a severity bucket (`aligned`, `notable`, `significant`, `extreme`). A separate pass scans Markdown and shell files for catalogued phrasing and structural patterns; a third pass measures doc-to-code, diagram inventory, and comment-density ratios. The three streams combine into a single overall signal label. See [docs/methodology.md](docs/methodology.md) for the full breakdown of metrics, fingerprint patterns, disproportion heuristics, and severity scoring.

## Contributing

Run tests with `pytest`. Lint with `ruff check .`. PRs are welcome; please run the full test suite before submitting, and keep new prose in the docs framed comparatively (signals, divergence, indicators) rather than as detection language.

## License

Apache 2.0. See `LICENSE`.
