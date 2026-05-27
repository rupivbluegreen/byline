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

## Commit history forensics (§5.2)

v0.2 introduces a commit-history pass that reads the candidate's git log directly and reports four sub-findings. None of them is a verdict; each is a comparative-attribution signal layered alongside the stylistic and structural ones already covered above. The pass runs only when the submission is a real git repository. A plain directory of files (extracted from a zip, copied without `.git`, or pulled via the GitHub archive endpoint) returns a zeroed `HistoryFindings` result; the `--no-history` flag on `byline audit` makes the skip explicit when the input is known to lack history.

### Timeline burst detection

The timeline check walks every commit in chronological order and slides a one-hour window across the commit timestamps. For each window position it counts commits whose timestamps fall inside the window. The maximum window count divided by the total commit count is `burst_density`. A repo is labelled `bursty` when `burst_density > 0.7` AND `total_commits >= 5`.

The intuition: a project that was iterated on over several sessions tends to spread its commits across multiple hours. A repository whose initial scaffolding, feature additions, and final polish all landed inside a single hour-long burst looks more like a paste-and-tweak workflow than a multi-session build. The 5-commit minimum prevents a two-commit repo from trivially exceeding the density threshold.

### First-commit paste detection

The first commit in chronological order is profiled against two heuristics. The first checks raw scale: did the initial commit touch at least 5 files AND insert at least 200 lines? The second checks proportional weight: did the initial commit's insertions account for more than 70 percent of the current HEAD line count? Either condition flips `first_commit_appears_pasted` to `True`.

A real repo usually begins with a `README.md` and a small scaffold; substantive code arrives in subsequent commits. A first commit that already contains the bulk of the project is consistent with a workflow where the candidate generated the project elsewhere and committed it whole.

### Commit-message style profiling

The pass collects the first lines of every non-merge commit (merge commits are excluded because their messages are auto-generated and would dilute the author-voice signal), concatenates them, and runs `metrics_for_text` over the result. The resulting `StyleProfile` is compared to the README's profile via Euclidean distance in the same 4-D normalized vector used by the within-repo self-baseline check: `(em_dash_density / 100, sophistication_score, typo_rate / 50, type_token_ratio)`. The distance is reported as `self_baseline_divergence`.

A repo where the README reads like polished marketing copy but the commit messages are terse and casually typed shows up here. The reverse case (formal commits, casual README) is equally informative. The metric is symmetric; it surfaces divergence, not direction.

A second commit-message signal is `debug_commit_ratio`: the fraction of messages whose first line matches `\b(fix|typo|wip|oops|argh|broken|undo|revert|whoops|nit|tmp|hack)\b`. A high ratio is a signal that the candidate was genuinely iterating; a zero ratio in a large repo is unusual.

### Author identity drift detection

The pass collects every unique author email and author name across the commit history. `drift_detected` flips to `True` when more than one unique email appears AND those emails span more than one distinct domain. Two emails on the same domain (for example a personal address and a bot address under the same organisation) do not trigger drift; they look like one person with multiple identities. Cross-domain drift, by contrast, suggests the history was authored by multiple people or constructed under multiple identities.

### File evolution

For a curated set of files (the README, any `docker-compose*.yml` / `.yaml`, the three largest shell scripts, and the three largest source files across Python, JavaScript, TypeScript, Go, and Rust), the pass walks every commit that touched the file and records the largest single-commit insertion count. The ratio `largest_single_addition_lines / current_total_lines` exceeding `0.8` flips `appears_pasted` to `True` for that file.

A file that grew incrementally over many commits has a small ratio; a file that arrived whole in one commit has a ratio near 1.0. The candidate-file selection is deliberate: README, compose files, and large scripts are the surfaces most likely to be pasted from a generator, while smaller utility files are noisier and excluded.

## Documentation-implementation alignment (§5.3)

A README is a promise. Code is the delivery. When the README documents a flag, env var, command, or dependency that the code never references, that gap is comparative signal worth raising. The alignment module runs in two complementary modes.

### Deterministic mode

The deterministic pass extracts four classes of artefacts from the README, then walks the repository to confirm each one has a plausible reference somewhere.

* **CLI flags**: every `--flag-name` mention in the README. Each documented flag is searched for as a literal substring in `.py` files. Typer/Click defaults (`--help`, `--version`) are skipped because they are provided by the framework rather than the project. A documented flag with no Python-side reference produces a `cli_flag_documented_missing_in_code` check at severity `notable`.

* **Env vars**: every `[A-Z][A-Z0-9_]{2,}` token in the README that has plausible env-var context (preceded by `$` or `${`, near the words `env`, `environ`, `getenv`, or `export`, or inside a fenced code block). A stoplist drops common prose abbreviations (`API`, `JSON`, `URL`, `HTTP`, etc.). Each surviving token is searched for in `.py`, `.sh`, `.yml`, `.yaml`, `.toml`, `.cfg`, `.ini`, `.env*`, and `docker-compose*` files using the usual env-var access shapes (`os.environ["X"]`, `os.getenv("X")`, `$X`, `${X}`, `X=`). A missing reference produces an `env_var_documented_missing_in_code` check at severity `notable`.

* **Commands**: every `./script.sh`-style token at the start of a code-block line. System tools (`docker`, `pip`, `npm`, etc.) cannot be checked against the repo and are skipped. A documented `./foo.sh` that does not exist on disk produces a `command_documented_missing_file` check at severity `significant`.

* **Dependencies**: every package name pulled from `pip install`, `npm install`, `yarn add`, or `pnpm add` calls in code blocks, plus bare package-name lines under Install / Requirements / Dependencies headings. Each is checked against the contents of `pyproject.toml`, `requirements.txt`, `package.json`, `go.mod`, and `Cargo.toml`. A documented dependency not appearing in any manifest produces a `dependency_documented_missing_in_manifest` check at severity `notable`.

### Semantic mode

The `--with-llm` flag enables a complementary Claude-driven pass. The semantic pass receives the README text, the documented-artefact list extracted by the deterministic mode, and a small representative sample of source files (entry points like `main.py` or `cli.py`, files mentioned by name in the README, plus the largest remaining source files, capped at 5 files and 200 lines each). Claude is prompted to identify three classes of gap that deterministic checks cannot surface:

* features described in the README that are not implemented in the code,
* behaviours implemented in the code that the README does not document,
* configuration values documented but not actually consumed.

The model returns a structured list of `AlignmentCheck` entries (each tagged `source="llm"`) that are merged with the deterministic checks. When the semantic pass is unavailable (no `[llm]` extra installed, no `ANTHROPIC_API_KEY`, or the model returns malformed output) the audit degrades silently to deterministic-only and sets `deterministic_only=True` on the result.

### Severity rollup

All checks (deterministic plus semantic) are classified as `notable` or `significant`. The rollup is:

* 0 significant AND at most 1 notable -> `aligned`
* 1 significant OR 2-4 notable -> `minor_gaps`
* 2+ significant OR 5+ notable -> `significant_gaps`

The rollup exists to prioritise reviewer attention. A single notable gap is not a flag; an accumulation of them is worth a closer look.

## Within-repo self-baseline (§5.4)

A single repository typically contains three distinct writing surfaces produced by the same author: commit messages, the README, and code comments. When the author is one person writing consistently, those surfaces share stylistic fingerprints. When the surfaces diverge sharply, that within-repo divergence is a comparative-attribution signal independent of any external baseline.

The check computes a `StyleProfile` for each surface, projects each profile onto a 4-dimensional normalized vector, and measures Euclidean distances between two pairs.

### Surfaces

* **Commit messages**: first lines of every non-merge commit, concatenated. Merge messages are excluded because they are typically auto-generated. A non-git directory yields an empty commit surface, which collapses the commit-vs-README distance to a fixed value and reduces the test to README-vs-comments.

* **README**: `README.md` at the repo root, read as UTF-8 with replacement.

* **Code comments**: comment lines harvested from every recognised source file in the repo (`.py`, `.sh`, `.js`, `.ts`, `.go`, `.rs`, `.rb`, `.c`, `.h`, `.cpp`, `.hpp`, `.java`, plus the JSX/TSX/zsh/bash variants), with comment-prefix tokens stripped. For Python files, triple-quoted string bodies (docstrings, by approximation) are also harvested. Generated directories (`.git`, `.venv`, `node_modules`, `vendor`, `__pycache__`, `dist`, `build`) are skipped.

### Distance

Each surface yields a `StyleProfile`. The 4-D normalized vector is:

```
(em_dash_density / 100, sophistication_score, typo_rate / 50, type_token_ratio)
```

The first and third coordinates are scaled so that a saturating real-world value maps to roughly 1.0; the second and fourth are already in [0, 1]. Two distances are reported:

* `commit_msg_vs_readme_distance`
* `code_comment_vs_readme_distance`

The README is the reference because it is the surface most commonly targeted for stylistic rewriting; comparing against it makes the divergence direction interpretable.

### Severity thresholds

The classification uses the larger of the two distances:

* `< 0.3` -> `consistent` (both surfaces look like the README author)
* `< 0.6` -> `notable` (one surface diverges moderately)
* `>= 0.6` -> `significant` (one surface diverges sharply)

The `note` field on the finding renders both distances as a short sentence so the reviewer can see which surface drove the verdict.

## Voice and AI-use disclosure (§5.5)

Two signals about how the author describes their own work. Both are evaluated against `README.md`.

### First-person voice

The check counts whole-word occurrences of first-person pronouns, with two important rules: the standalone `I` is matched case-sensitively (lowercase `i` is almost always a loop variable, not a pronoun); and `my`, `mine`, `me` plus the contracted forms `I'm`, `I've`, `I'd`, `I'll` are matched case-insensitively as whole words. Fenced code blocks are stripped before counting so example snippets that happen to contain pronouns do not inflate the result.

The reported metrics are `first_person_count` (the raw count) and `first_person_per_1k_words` (the density normalised against the prose word count after code-block stripping). When density exceeds 2.0 occurrences per 1000 words, `has_first_person_voice` flips to `True`.

A README written in first person tends to read like the author's own voice. The presence signal is treated as positive: authors describing their work in their own voice produce prose identifiably theirs. Absence is informative too, but absence is not damning on its own; many style guides discourage first person in technical writing.

### AI-use disclosure

The check inspects a small set of conventionally-disclosure-bearing filenames in case-insensitive directory order: `README.md`, `AI_USE.md`, `ATTRIBUTION.md`, `AUTHORS.md`, `DISCLOSURE.md`. Within each file, the scanner looks for two regex patterns:

* A tool / model mention: `\b(claude|chatgpt|gpt[- ]?\d|copilot|cursor|llm|ai[- ]?(?:assist|generated|help))`
* A use verb: `\b(used|with help of|assisted|generated|wrote|drafted)`

When a tool mention and a use verb co-occur within 50 characters of each other, the pair counts as a disclosure. The matched sentence (extended out to surrounding sentence boundaries, trimmed to 200 characters) is captured as the excerpt.

Disclosure is treated as a positive trust signal in the overall scoring. An author who voluntarily documents where AI tools contributed is giving the reviewer information they can act on; that transparency shifts the overall label toward `aligned` rather than away from it.

## Boilerplate density (§5.6)

Real long-lived projects accumulate cultural metadata over time: `CONTRIBUTING.md` because someone asked how to contribute, `SECURITY.md` because someone reported a vulnerability, `.editorconfig` because two contributors disagreed about indentation. The result is a partial, organic set of meta-files that grows by accident, not by template. Repositories scaffolded in a single sitting often land the full canonical set in one shot, because that is what a template or an assistant emits.

### Files checked

The canonical slate is 10 entries:

* `.editorconfig`
* `.github/ISSUE_TEMPLATE`
* `.github/PULL_REQUEST_TEMPLATE.md`
* `.github/CODEOWNERS`
* `CODE_OF_CONDUCT.md`
* `CONTRIBUTING.md`
* `SECURITY.md`
* `SUPPORT.md`
* `.pre-commit-config.yaml`
* `.gitattributes`

Directory entries (like `.github/ISSUE_TEMPLATE`) count as present only when they contain at least one file.

### Density and severity

`density_ratio = present_count / 10`. The base severity is:

* `density < 0.4` -> `normal`
* `0.4 <= density < 0.7` -> `notable`
* `density >= 0.7` -> `significant`

### Small-project bump

A fully populated meta-file set in a tiny codebase is more notable than the same set in a large mature one. When the repository's total recognised source LOC is greater than zero but less than 1000, the severity is bumped up one step. The bump is applied once; `significant` does not climb beyond itself. Repos with zero source files are not considered small and do not receive the bump.

## Overall signal scoring (extended for v0.2)

The v0.1 overall-signal heuristic combined three streams (deltas, fingerprints, disproportions) into an ordinal label. v0.2 layers five additional finding categories on top via additive adjustment. The base index is computed from the v0.1 rules and then nudged:

```
index 0 = aligned
index 1 = mixed
index 2 = divergent
index 3 = highly_divergent
```

The v0.2 adjustments are:

* `history.timeline.bursty` -> +1
* `history.timeline.first_commit_appears_pasted` -> +1
* `history.messages.self_baseline_divergence > 0.5` -> +1
* `history.identity.drift_detected` -> +2 (strong push toward `highly_divergent`)
* `alignment.overall_alignment == "significant_gaps"` -> +1
* `voice.has_first_person_voice == False` -> +1
* `voice.ai_disclosure_found == True` -> -2 (strong pull toward `aligned`)
* `boilerplate.severity == "significant"` -> +1
* `self_baseline.within_repo_divergence == "significant"` -> +1

After all adjustments are applied, the index is clamped to `[0, 3]` and mapped back to the corresponding label. The asymmetric weighting of `voice.ai_disclosure_found` is deliberate: disclosure is a positive trust signal that, on its own, can override mild divergence elsewhere. The asymmetric weighting of `history.identity.drift_detected` reflects the fact that multi-domain author churn in a single repo is one of the harder signals to explain away innocently.

The combined label remains ordinal and conservative. A clean `aligned` result still means no individual stream produced enough signal to warrant a flag; it does not mean the candidate wrote every line. The v0.2 additions widen the set of inputs but do not change the framing: reviewer judgement matters more than the score.
