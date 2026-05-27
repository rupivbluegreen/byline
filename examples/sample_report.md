# byline — Comparative Attribution Report

- **Target repo:** https://github.com/octocat-example/take-home-submission
- **Candidate:** octocat-example
- **Generated:** 2026-05-27T15:30:00+00:00

> This report presents stylistic signals comparing a candidate's submission to their own observable writing baseline. It is one input into a hiring decision, never a determination of authorship, and must not be treated as evidence of misconduct. False positives are possible — non-native English writers, proofread submissions, tutorial-derived code, and team-authored repos can all produce divergent signals.

## Overall signal

**Overall signal: mixed.** Some metrics diverge from baseline while others align; treat as a soft signal worth a closer look.

## Baseline profile

Baseline aggregated from 4 repo(s), 2174 total words.

## Target profile

Target style profile computed from the repository's Markdown and prose comments.

- **em_dash_density**: 9.412
- **emoji_in_headers_ratio**: 0.500
- **avg_sentence_length**: 21.300
- **type_token_ratio**: 0.482
- **typo_rate**: 0.460
- **sophistication_score**: 0.298
- **banner_comment_density**: 4.150
- **progress_ux_score**: 0.750

## Comparative deltas

| Metric | Baseline | Target | Δ absolute | Δ relative | Severity |
|---|---|---|---|---|---|
| em_dash_density | 3.110 | 9.412 | +6.302 | +202.6% | significant |
| emoji_in_headers_ratio | 0.080 | 0.500 | +0.420 | +525.0% | extreme |
| avg_sentence_length | 19.400 | 21.300 | +1.900 | +9.8% | aligned |
| type_token_ratio | 0.471 | 0.482 | +0.011 | +2.3% | aligned |
| typo_rate | 2.880 | 0.460 | -2.420 | -84.0% | significant |
| sophistication_score | 0.265 | 0.298 | +0.033 | +12.5% | aligned |
| banner_comment_density | 0.900 | 4.150 | +3.250 | +361.1% | significant |
| progress_ux_score | 0.250 | 0.750 | +0.500 | +200.0% | significant |

## Fingerprint findings

### README.md

- **phrase** / unlock the potential: "...designed to unlock the potential of your data pipeline..."
- **phrase** / harness the power: "...harness the power of asynchronous workers..."
- **structure** / emoji_header_cluster: 6 emoji-prefixed headers — L3: Overview; L18: Features; L42: Quickstart; L71: Architecture; L92: Configuration; L118: Notes

### scripts/setup.sh

- **shell_banner** / banner_comment: # ==============================
- **progress_ux** / progress_marker: echo "[1/4] Installing dependencies"
- **structure** / what_it_does_block: # WHAT IT DOES: bootstraps the dev environment

## Disproportion findings

- **doc_to_code_ratio** (notable): observed 0.610 vs threshold 0.500. Documentation-to-code ratio of 0.61 (842 doc lines / 1380 code lines) is a structural signal of how prose volume compares to working code; treat as a comparative indicator, not a verdict.
- **diagram_count** (notable): observed 4.000 vs threshold 4.000. 4 diagram/architecture image(s) found in a 1380-LOC project; a heavy diagram inventory relative to code is a comparative-attribution signal, not a verdict.
- **comment_density** (significant): observed 0.420 vs threshold 0.250. Overall comment density of 0.42 (411 comment lines / 980 source lines) is a structural signal of explanatory-prose volume in code; treat as a comparative indicator, not a verdict.

## Qualitative interpretation

> The submission's writing surface diverges from the candidate's observable baseline along several axes. Em-dash density is roughly three times the baseline rate, the README leans heavily on emoji-prefixed section headers where the candidate's prior repositories use plain headings, and the setup script carries banner-comment scaffolding and `[N/M]` progress markers that are absent from the candidate's own historical shell work. The typo rate has dropped sharply versus baseline, which is consistent with a heavily proofread or differently-authored draft. Sentence length, lexical diversity, and vocabulary sophistication are broadly aligned, so the divergence is concentrated in surface scaffolding rather than underlying prose rhythm. A follow-up conversation about how the README and install scripts came together would be a reasonable next step; none of these signals on their own establishes anything about authorship.

## Methodology

Signals are computed by comparing eight stylistic metrics on the target repository against the candidate's aggregated writing baseline, then cross-referenced against a catalogue of known phrasing and structural patterns. Severities follow fixed thresholds; nothing here is a verdict.

See [docs/methodology.md](../docs/methodology.md) for full metric definitions, thresholds, and limitations.

---

_This report presents stylistic signals; it is not a determination of authorship._

_byline v0.1.0_
