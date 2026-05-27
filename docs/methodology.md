# Methodology

## Overview

`byline` produces comparative signals: it measures eight stylistic indicators on a candidate's submission, compares each to the same candidate's prior public GitHub writing, and surfaces the deltas alongside catalogued phrasing and structural patterns. Severities follow fixed thresholds. The output is reviewer-facing context, not a determination of authorship.

This document covers the eight style metrics (§7.1), the fingerprint catalogue (§7.2), the disproportion analyser (§7.3), the comparative delta scoring (§8), the overall-signal heuristic (§8), and the known limitations.

## Style metrics

Each metric is a pure function from text to a float. The aggregator `metrics_for_text` bundles them into a `StyleProfile` per surface (prose or shell).

### em_dash_density

What it measures: the rate at which em-dashes (`U+2014` or `--`) appear in prose, normalised per 1000 whitespace-split words.

How it's computed: `(count("—") + count("--")) / word_count * 1000`. Word count excludes pure-punctuation tokens so that the dashes themselves do not inflate the denominator.

Comparative use: heavy em-dash usage is a known stylistic tell. A target with density well above the candidate's baseline is a notable divergence; a target with density well below a chatty candidate's baseline is also worth flagging in the other direction.

Caveats: typographic preference varies. Some humans use em-dashes liberally; some keyboard layouts make them difficult. A high standalone reading is uninformative without the baseline comparison.

### emoji_in_headers_ratio

What it measures: the fraction of Markdown ATX headers whose first non-whitespace character is a Unicode emoji.

How it's computed: for each line matching `^#{1,6}\s+(.+)$`, check whether the captured group's first character falls within the emoji code-point ranges (Misc Symbols, Dingbats, Misc Technical, the main emoji blocks). Return matches divided by total headers; `0.0` when there are no headers.

Comparative use: emoji-prefixed headers are a common machine-drafted-README signal. Candidates who never use them in their public repos but suddenly produce a submission with six emoji-headed sections diverge meaningfully on this signal.

Caveats: some developers genuinely prefer emoji navigation in long docs. A consistent baseline of emoji headers makes the signal moot.

### avg_sentence_length

What it measures: mean words-per-sentence across the prose corpus.

How it's computed: delegated to `textstat.words_per_sentence` (with a fallback to the historical `avg_sentence_length` alias). Returns `0.0` for empty input.

Comparative use: sentence length is fairly stable within a single writer. A submission whose mean diverges sharply from the candidate's baseline is a stylistic signal worth raising.

Caveats: technical prose with many short code-adjacent fragments skews short. Long paragraphs of explanatory prose skew long. Genre differences within a single writer's corpus can produce noisy deltas.

### type_token_ratio

What it measures: lexical diversity, defined as unique tokens divided by total tokens.

How it's computed: lowercase the text, strip `string.punctuation`, split on whitespace, drop empties, then divide unique tokens by the total count.

Comparative use: lexical diversity tracks a writer's vocabulary range. Sharp divergence in either direction (much higher or much lower) is a signal.

Caveats: the metric is sensitive to corpus size. Small samples inflate the ratio mechanically. Short submissions versus a large baseline can produce misleading deltas.

### typo_rate

What it measures: grammar and spelling issues per 1000 whitespace-split words.

How it's computed: lazy `language_tool_python` initialisation; on each call, run the LanguageTool checker over the text and count matches. If the tool is unavailable (no JVM, no network for the model download), the signal degrades to `0.0` with a logged warning.

Comparative use: typos and grammar misses are one of the more reliable stylistic differentiators between human-edited and machine-drafted prose. A submission with a far cleaner typo rate than the candidate's baseline is a divergence worth noting.

Caveats: heavy proofreading and editor-grammar tooling produce the same signal as machine drafting. The metric is not a verdict; it is one input. The `0.0` fallback when LanguageTool is unavailable hides the signal entirely.

### sophistication_score

What it measures: fraction of tokens absent from a top-5000 common-words reference list.

How it's computed: tokenise as in `type_token_ratio`, then count tokens not present in `byline/data/common_5000.txt` and divide by total tokens. Returned as a 0.0–1.0 fraction (the spec phrases it as a percentage; the implementation returns the fraction).

Comparative use: vocabulary sophistication varies less than people expect within a single author. A submission with markedly more uncommon vocabulary than the candidate's baseline is a stylistic divergence.

Caveats: jargon-heavy technical writing inflates the score. Domain-specific submissions are not directly comparable to a baseline drawn from general-purpose prose.

### banner_comment_density

What it measures: shell-script banner-comment lines per 100 script lines. A banner is a line like `# =====` or `# -----` (5 or more separator characters).

How it's computed: line-anchored regex matching `^\s*#?\s*[=\-#]{5,}\s*$` across the script text. Count matches, divide by total lines, multiply by 100. `0.0` for empty input.

Comparative use: dense banner-comment usage in shell scripts is a known machine-drafted scaffolding pattern. A candidate with no banners in their public scripts who submits a script full of them is a divergent case.

Caveats: some shell-script style guides (and some authors) genuinely prefer banner comments. The signal needs the baseline to be useful.

### progress_ux_score

What it measures: heuristic 0.0–1.0 score capturing UX-scaffolding patterns in shell scripts: `[N/M]` progress markers, `Expected output:`, `Next steps:`, `What this does:`, `When to run:`, `Setup complete!`, `Cleanup complete!`.

How it's computed: count distinct signal markers present in the script, return `min(1.0, signals / 4.0)` so four distinct signals saturate the score.

Comparative use: heavy progress-UX scaffolding is another machine-drafted signal. Candidates whose own scripts are bare and whose submission script is heavily annotated diverge sharply.

Caveats: install scripts and operator runbooks often include this scaffolding for legitimate operational reasons. Genre and intent matter.

## Fingerprint library

Three structural signals plus a phrase catalogue.

### Phrase patterns and weights

`byline/data/ai_phrases.json` enumerates 45+ catalogued phrases and patterns associated with machine-drafted prose. Each entry carries a `pattern`, a `type` (`literal` or `regex`), a `weight` in `[0.0, 1.0]`, and a brief `note` documenting the rationale. Literals are matched case-insensitively after `re.escape`; regex entries are compiled directly. Every occurrence in the text produces a `FingerprintHit` with the file path, line number, pattern, category (`phrase`), and a short excerpt around the match. Weights are surfaced for downstream tooling that wants to rank hits; the v0.1 overall-signal heuristic uses hit counts rather than weighted sums.

### Section-header cluster detection

`byline/data/ai_section_headers.json` catalogues common machine-drafted section header strings. The scanner iterates ATX-style headers in the document, matches each against the catalogue, and emits a single `ai_section_header_cluster` hit when six or more catalogued headers are present in one file. The cluster threshold avoids one-off false positives.

### Emoji-header cluster detection

Independent of the catalogue. When four or more headers in a single document begin with an emoji character (per the same code-point ranges used by `emoji_in_headers_ratio`), the scanner emits an `emoji_header_cluster` hit. The excerpt lists the matching headers with line numbers.

### Em-dash overdose

When `em_dash_density` exceeds 8.0 per 1000 words, the scanner emits an `em_dash_overdose` structural hit. The threshold is calibrated against typical human technical writing; everyday human-authored READMEs come in well below.

### Shell script signals

Banner comments (`^\s*#?\s*[=\-#]{5,}\s*$`), `[N/M]` progress markers, `WHAT IT DOES:` / `WHEN TO RUN:` block headers, and printf-banner closers (`Setup complete!`, `Cleanup complete!`, `Installation complete!`) each produce per-occurrence `FingerprintHit` entries with category `shell_banner`, `progress_ux`, or `structure` as appropriate.

## Disproportion analysis

Four detectors operate on the repository's structural shape.

### doc_to_code_ratio

Sums `*.md` and `*.rst` line counts and divides by total source-code line counts. Lockfiles, vendored trees, build artefacts, virtualenvs, and binary blobs are excluded. Thresholds:

| Observed | Severity |
|---|---|
| > 1.0 | significant |
| > 0.5 | notable |
| otherwise | info |

The reported reference threshold on the finding is 0.5 (the notable boundary).

### diagram_count

Counts diagram-style images that live under `docs/` or whose path contains `diagram` or `architecture`. Image extensions considered: `.png`, `.svg`, `.jpg`, `.jpeg`, `.gif`. Thresholds depend on project size:

| Project LOC | Notable | Significant |
|---|---|---|
| < 2000 | >= 4 images | >= 6 images |
| >= 2000 | >= 2 per 1000 LOC | >= 3 per 1000 LOC |

### numbered_diagram_pattern

Looks for filenames matching `diagram-\d{1,3}[-_].+\.(png|svg|jpg|jpeg|gif)`. The observation is the length of the longest consecutive integer run found across the matched filenames. Thresholds:

| Longest run | Severity |
|---|---|
| >= 5 | significant |
| >= 3 | notable |
| otherwise | info |

A tightly sequenced numbered diagram set is a structural signal of templated scaffolding.

### comment_density

Aggregates comment lines across all recognised source files. Line comments (`#`, `//`, `--`) and lines inside C-style `/* ... */` blocks both count. The observation is total comment lines divided by total source lines. Thresholds:

| Observed | Severity |
|---|---|
| > 0.40 | significant |
| > 0.25 | notable |
| otherwise | info |

The reported reference threshold on the finding is 0.25.

## Comparative delta scoring

For each of the eight `StyleProfile` fields, `compute_deltas` produces a `ComparativeDelta` with:

- `absolute_delta = target_value - baseline_value`
- `relative_delta = absolute_delta / baseline_value` when the baseline is positive
- `relative_delta = 0.0` when both baseline and target are zero
- `relative_delta = ±10.0` when the baseline is zero but the target is non-zero (the zero-baseline sentinel)

The sentinel value `10.0` replaces `float('inf')` so the result serialises cleanly through Pydantic and JSON. The sign of the sentinel reflects the sign of the target value.

Severity is derived from the magnitude of `relative_delta`:

| `abs(relative_delta)` | Severity |
|---|---|
| < 0.25 | aligned |
| < 0.75 | notable |
| < 2.0 | significant |
| >= 2.0 | extreme |

Severities are reported per metric in the report. Reviewers can scan the deltas table and see which metrics diverge and by how much.

## Overall signal heuristic

The three signal streams (deltas, fingerprints, disproportions) combine into a single label via `overall_signal`. Counts:

```
extreme_deltas      = number of deltas with severity == "extreme"
significant_deltas  = number of deltas with severity in {"significant", "extreme"}
n_fp                = total fingerprint hits across the repo
n_signif_disp       = disproportions with severity == "significant"
n_notable_disp      = disproportions with severity in {"notable", "significant"}
```

The decision rules fire in order; the first match wins:

1. `extreme_deltas >= 1` AND `n_fp >= 8` AND `n_signif_disp >= 1` -> `highly_divergent`
2. `significant_deltas >= 4` OR `n_fp >= 8` OR `extreme_deltas >= 1` -> `divergent`
3. `significant_deltas >= 2` OR `n_fp >= 4` OR `n_notable_disp >= 1` -> `mixed`
4. otherwise -> `aligned`

The labels are deliberately ordinal and conservative. A clean `aligned` result means no individual stream produced enough signal to warrant a flag; it does not mean the candidate wrote every line.

## Limitations

The same false-positive cases that the README enumerates apply to every signal:

- Non-native English writers show shifted typo rates, sentence-length distributions, and vocabulary sophistication versus baselines drawn from native-language prose.
- Candidates who proofread submissions heavily can produce a low typo rate that diverges from their casual GitHub baseline.
- Tutorial-derived code and shell scripts carry the tutorial author's voice.
- Team-authored repositories blend multiple writers; the aggregate baseline is a poor reference.
- Candidates with sparse public GitHub history have small baselines. Below roughly 500 words of baseline prose, the per-metric deltas are noisy and should be treated as unreliable. The report flags total word count in the baseline section so reviewers can judge.

Beyond false positives, the tool is gameable. A candidate who knows `byline` is part of the review can adjust their style. The tool is most useful when the comparison is run silently, and when the reviewer treats a clean report as the absence of signal rather than positive evidence of anything.

Finally: nothing in the methodology asserts authorship. Every signal is comparative divergence between two writing surfaces. The report exists so reviewers have one more structured input when deciding whether a follow-up conversation is warranted. Reviewer judgement matters more than the score.
