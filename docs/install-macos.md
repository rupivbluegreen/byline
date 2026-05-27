# Installing byline on macOS

`byline` is a Python CLI for comparative attribution analysis of GitHub take-home submissions. It compares a candidate's submission against their own GitHub baseline and reports stylistic divergence. This guide covers a clean install on macOS, the credentials it expects, and the rough edges to know about.

## Prereqs

- **Python 3.10 or newer.** The system Python on macOS is fine in principle, but a Homebrew install is cleaner:

  ```bash
  brew install python@3.12
  ```

- **JRE 17 or newer.** The `audit` command (and any run that touches the typo metric) starts a local LanguageTool server, which is Java. Temurin via Homebrew works:

  ```bash
  brew install --cask temurin
  java -version
  ```

- **`gh` CLI (optional, recommended).** Convenient for minting a GitHub token without copy-pasting it from the website:

  ```bash
  brew install gh
  gh auth login
  ```

## Install

The cleanest install is `pipx`, which puts `byline` in its own isolated environment and exposes the command on your `PATH`:

```bash
brew install pipx
pipx ensurepath
pipx install byline
```

If you want the optional Claude qualitative pass (`--with-llm`), install the `[llm]` extra. `pipx` accepts extras inline:

```bash
pipx install "byline[llm]"
```

Already installed and adding the extra later:

```bash
pipx inject byline anthropic
```

If you prefer plain `pip` in a virtualenv:

```bash
python3.12 -m venv ~/.venvs/byline
source ~/.venvs/byline/bin/activate
pip install "byline[llm]"
```

Verify the install:

```bash
byline --help
```

## Tokens

`byline` reads two environment variables. Neither is strictly required, but both unblock common cases.

**`GITHUB_TOKEN`** — used when fetching a candidate's baseline corpus. Anonymous GitHub API calls are capped at 60 requests per hour, which a single `baseline` run will burn through. With `gh` installed:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

Add that line to `~/.zshrc` (or your shell's rc file) if you want it persistent. Alternatively, pass it per-invocation with `--github-token`.

**`ANTHROPIC_API_KEY`** — required only if you pass `--with-llm`. Get a key from the Anthropic console and export it:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Only aggregated stylistic statistics are sent to the API. Raw README and shell-script text stay local.

**Security.** Don't commit either key. Don't paste them into shared shells or screenshots. If a key leaks, rotate it immediately at the issuing console (`gh auth refresh` for GitHub, the Anthropic console for the API key).

## First run

If you've never used `byline` before, start with the wizard. It walks through the same flags as `scan` and `audit` but with prompts:

```bash
byline wizard
```

It will ask for:

- mode (`scan` or `audit`)
- the target path (the candidate's submission directory)
- the candidate's GitHub username (for `audit`)
- output paths (Markdown, `.docx`, or JSON)
- whether to enable `--with-llm`

The wizard prints the equivalent non-interactive command before running, so you can copy it for next time.

## All commands

**`scan`** runs fingerprints and disproportion analysis against the submission alone, without pulling a baseline. Use it when you want a quick read on the submission's own stylistic surface, or when the candidate has no public GitHub history to compare against.

```bash
byline scan ./candidate-submission
```

**`baseline`** fetches and profiles a candidate's prior public GitHub writing, then prints (or saves) the resulting `StyleProfile`. Useful for inspecting the baseline before committing to a full comparison, or for caching the baseline for later reuse.

```bash
byline baseline candidate-username
```

**`audit`** is the full comparative pass: build the baseline, profile the submission, compute per-metric deltas, run fingerprints and disproportion checks, and emit a single combined report. This is the command most reviewers will use.

```bash
byline audit ./candidate-submission --candidate candidate-username
```

**`wizard`** is the interactive front end described above. It dispatches to `scan` or `audit` based on the mode you pick.

```bash
byline wizard
```

## Output formats

By default, every command writes a Markdown summary to stdout. Three flags change that:

- `--output PATH` — write the Markdown report to a file instead of stdout.
- `--docx PATH` — write a Word-compatible `.docx` report. Opens in Word, Pages, or LibreOffice:

  ```bash
  byline audit ./submission --candidate username --docx report.docx
  open report.docx
  ```

- `--json` — emit the structured report as JSON to stdout. Useful for piping into other tooling.

`--json` and `--docx` are mutually exclusive. Pick one per invocation.

## Gotchas

**LanguageTool first-run download.** The first `audit` (or any command that exercises the typo metric) downloads LanguageTool 6.8 — roughly 259 MB — into `~/.cache/language_tool_python/` and starts a local Java server. Expect a noticeable delay on first run and confirm a JRE 17+ is on your `PATH` before kicking it off. Subsequent runs reuse the cached download.

**GitHub rate limits.** Anonymous API access (60 requests/hour) is not enough for a real `baseline` or `audit`. Set `GITHUB_TOKEN` (see above) before running against any non-trivial candidate. A token bumps the limit to 5,000 requests/hour.

**Key rotation.** Treat `GITHUB_TOKEN` and `ANTHROPIC_API_KEY` like passwords. If you suspect either has leaked — shared terminal, accidental commit, screen-shared shell — rotate immediately. Don't wait.

**`--with-llm` requires the extra.** If you installed plain `byline` without `[llm]`, the `--with-llm` flag will error out. Reinstall with the extra (`pipx install --force "byline[llm]"`) or inject the dependency (`pipx inject byline anthropic`).

## Uninstall

Remove the CLI:

```bash
pipx uninstall byline
```

If you used a venv instead, just delete the venv directory.

The LanguageTool cache lingers after uninstall. Remove it if you want the disk space back:

```bash
rm -rf ~/.cache/language_tool_python/
```

GitHub and Anthropic tokens in your shell rc file are unaffected by uninstall; remove those manually if you no longer need them.
