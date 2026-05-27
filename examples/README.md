# Examples

This directory contains illustrative output from `byline`. Nothing here describes a real candidate.

## `sample_report.md`

A sanitised example of the full 11-section comparative report that `byline audit` produces. The target repository, candidate handle, and signal values are placeholder data; the candidate name is `octocat-example` and the target is a fictional take-home submission. The report shape, section ordering, and disclaimer text match what the renderer emits for a real audit.

## How to read it

Walk top to bottom. The header lists the target and candidate. The verbatim disclaimer is rendered once in section two. The overall signal label appears next, followed by the baseline and target profiles, the per-metric comparative deltas table, fingerprint findings grouped by file, disproportion findings, the optional qualitative paragraph, and a methodology pointer. The footer carries the tool version.

The values shown blend `aligned`, `notable`, and `significant` severities so that the structure is visible without implying any one finding is conclusive. Real reports will look different; this one is just a shape reference.

## Generating your own

```bash
byline audit https://github.com/your-org/take-home-submission \
  --candidate candidate-username \
  --output report.md
```

Run `byline audit --help` for the full flag list, including the `[llm]` qualitative-pass toggle.
