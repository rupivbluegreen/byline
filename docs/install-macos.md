# Installing byline-audit on macOS

A step-by-step setup walkthrough for a fresh MacBook. The reader is assumed to have a terminal open and nothing else.

## 1. What you're installing

`byline-audit` is a comparative attribution analysis tool for hiring reviewers. It reads a candidate's take-home submission, builds a stylistic baseline from that candidate's public GitHub writing, and reports where the two diverge as a set of signals for human review. For the full framing of what the tool is and what it is not, see the project [README](../README.md).

This guide covers the macOS-specific bits: Python, the CLI, the GitHub token, the optional Claude key, and a first audit run.

## 2. Prerequisites

- macOS 12 Monterey or later. Both Intel and Apple Silicon Macs are supported via universal Python wheels.
- A terminal. Terminal.app from `/Applications/Utilities/` works; iTerm2 is fine too.
- Python 3.10 or newer. Recent macOS releases ship a system Python at version 3.9, which is too old for byline. Check what you have:

```bash
python3 -V
```

If the output is `Python 3.9.x` or older, follow the next section. If it already prints 3.10 or higher, skip ahead to the byline install in step 4.

## 3. Install Python 3.11+ on macOS

Pick one of the two paths below.

### Path A: Homebrew (recommended for most users)

Homebrew is the standard package manager for macOS and the path most engineers already have. Install it if it isn't on the machine, then install Python:

```bash
# Install Homebrew (skip if brew is already on this machine)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.12
brew install python@3.12

# Verify
python3.12 -V
```

### Path B: Official Python installer

If you would rather not install Homebrew, the python.org installer is a self-contained `.pkg`:

1. Open https://www.python.org/downloads/macos/.
2. Download the macOS 64-bit universal2 installer for Python 3.12 (or any 3.10+).
3. Double-click the `.pkg` and follow the prompts.
4. Open a new terminal and confirm `python3 -V` reports the new version.

The rest of this guide uses `python3.12` in commands. If you installed a different minor version (3.11, 3.13), substitute it where you see `python3.12`.

## 4. Install byline-audit

A note on naming, because it will trip you up otherwise: the package on PyPI is published as `byline-audit` because the bare `byline` name on PyPI was taken by an unrelated abandoned project. After installation, the import name and the command-line entry point are both `byline`. So you `pip install byline-audit`, then you run `byline`.

Pick the base or the full install:

```bash
# Base install. All deterministic checks work; no LLM features.
python3.12 -m pip install --user byline-audit

# Full install. Adds the LLM-powered subcommands (requires ANTHROPIC_API_KEY).
python3.12 -m pip install --user 'byline-audit[llm]'
```

The user-install flag (the one that says `user` in the pip command above) drops the package into your home directory and avoids needing sudo. The trade-off is that `pip` may put the `byline` executable somewhere that isn't on your `PATH`. If the install completes but the shell cannot find `byline`, fix the `PATH`:

```bash
# Find the user scripts directory
python3.12 -m site --user-base

# Add its bin/ to PATH in ~/.zshrc (the default macOS shell since Catalina)
echo 'export PATH="$(python3.12 -m site --user-base)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

A cleaner alternative is a per-project virtual environment. This sidesteps the `PATH` question entirely and isolates byline's dependencies from anything else on the system:

```bash
mkdir ~/byline-workspace && cd ~/byline-workspace
python3.12 -m venv .venv
source .venv/bin/activate
pip install 'byline-audit[llm]'
which byline    # expect: ~/byline-workspace/.venv/bin/byline
```

While the venv is active, `byline` resolves to the one inside `.venv/bin`. Run `deactivate` when you're done.

## 5. Set up GitHub access

`byline` calls the GitHub REST API to assemble the candidate's baseline corpus from their public commits, README files, and shell scripts. Anonymous API calls are aggressively rate-limited (60 per hour from a single IP), which is not enough for a full baseline build. Set `GITHUB_TOKEN` to lift the cap to 5,000 per hour.

```bash
# Option 1: create a Personal Access Token at
# https://github.com/settings/personal-access-tokens
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Option 2: if the GitHub CLI is already installed
brew install gh
gh auth login         # follow the prompts
export GITHUB_TOKEN=$(gh auth token)
```

The token only needs the default public read scope. It does not need the `repo` write scope, and it does not need access to any private repositories. Add the `export GITHUB_TOKEN=...` line to `~/.zshrc` so it persists across shell sessions.

## 6. Set up Claude (only for LLM features)

The `byline questions` and `byline chat` subcommands, plus the optional semantic pass on `byline audit` and `byline align`, call an LLM API. Claude is the default. Skip this section if you only need deterministic features.

```bash
# Get an API key at https://console.anthropic.com/account/keys
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
```

As with the GitHub token, add this line to `~/.zshrc` for persistence:

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx' >> ~/.zshrc
echo 'export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' >> ~/.zshrc
source ~/.zshrc
```

**Want OpenAI or a self-hosted model instead?** `byline` also speaks the OpenAI API and any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, llama.cpp). The next section walks through the macOS setup and a verification command for each backend; the full reference is in [`docs/llm-providers.md`](llm-providers.md).

## 6a. Configure and verify an LLM provider on macOS

Pick one of the three options below. Each one ends with the same one-line smoke test: a 3-question `questions` run against the bundled synthetic fixture. If it prints three grounded questions, the provider is wired correctly.

### Option A: Claude (default)

```bash
# 1. Set the key (also recommended to add to ~/.zshrc)
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx

# 2. Verify
byline questions tests/fixtures/synthetic_ai_repo -n 3
```

If `byline` was installed via `pip install --user`, the `tests/` fixture isn't in the install directory. Either clone the repo (`git clone https://github.com/rupivbluegreen/byline && cd byline`) or substitute any small local directory you have on disk for the fixture path.

### Option B: OpenAI (GPT)

```bash
# 1. Get a key from https://platform.openai.com/api-keys
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx
export BYLINE_LLM_MODEL=gpt-4o          # optional, this is the default

# 2. Verify
byline questions tests/fixtures/synthetic_ai_repo -n 3
```

### Option C: Self-hosted via Ollama (runs locally, no cloud API)

```bash
# 1. Install and start Ollama
brew install ollama
ollama serve &                          # leaves Ollama running in the background

# 2. Pull a small model (3-4 GB; one-time download)
ollama pull llama3.2

# 3. Point byline at Ollama's OpenAI-compatible endpoint
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=ollama            # any non-empty string; Ollama ignores it
export OPENAI_BASE_URL=http://localhost:11434/v1
export BYLINE_LLM_MODEL=llama3.2

# 4. Verify
byline questions tests/fixtures/synthetic_ai_repo -n 3
```

Local models tend to be slower (a few seconds to a minute per call on CPU; faster on Apple Silicon). They may also drift more from the JSON-shaped response that `questions` expects, in which case byline logs a warning and drops the malformed entries.

### Switching providers later

The provider is resolved once per command run, from the env at process start. To swap between Claude and Ollama, either edit `~/.zshrc` and `source` it, or set the env vars inline for one command:

```bash
BYLINE_LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama BYLINE_LLM_MODEL=llama3.2 \
  byline questions tests/fixtures/synthetic_ai_repo -n 3
```

### What "wired correctly" looks like

A passing smoke test prints something like:

```markdown
# Interview Questions

## Q1. The Dockerfile bundles a banner comment block at the top — what does that scaffolding suggest about the operational maturity of the deployment, and how would you tighten it for production?
_Grounding: Dockerfile:1_
_Rationale: Banner comments are a stylistic signal flagged by the audit; this asks the candidate to defend or refine the convention._
_Signal: shell_banner_

...
```

If you instead see `error: This command requires the LLM extras and an API key`, your env vars aren't picked up yet — open a fresh shell or re-`source ~/.zshrc`.

## 7. Optional: install Java for typo detection

One of the eight stylistic metrics is `typo_rate`. It is computed with `language_tool_python`, which in turn requires a Java runtime. Without Java, byline reports a `typo_rate` of 0.0 and logs a warning; every other metric and every fingerprint check continues to work normally. If you want the typo metric live:

```bash
brew install openjdk@21
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-21.jdk
java -version
```

On Intel Macs the Homebrew prefix is `/usr/local/opt` rather than `/opt/homebrew/opt`; adjust the symlink path accordingly.

## 8. Verify the install

```bash
byline --version
# expect: byline 0.2.0
```

Then run `byline` with no arguments to see the subcommand list (`audit`, `baseline`, `scan`, `align`, `questions`, `chat`). Each subcommand also accepts `--help` for its full flag list.

If the shell answers with `command not found: byline`, the user-scripts bin directory is not on your `PATH`. Go back to step 4 and either fix the `PATH` or switch to a venv. A reliable fallback that works once the package is installed is `python3.12 -m byline` followed by the same arguments.

## 9. First audit: a complete walkthrough

The fastest way to confirm the install end to end is a fingerprint-only scan against a small directory. The byline test fixtures include a synthetic sample for exactly this:

```bash
# Just the target. No baseline, no GitHub calls.
byline scan tests/fixtures/synthetic_ai_repo
```

If you cloned the byline repo locally that path resolves; otherwise point at any directory that contains a README. For a real candidate submission:

```bash
git clone https://github.com/some-candidate/take-home-submission /tmp/sub
byline scan /tmp/sub
```

The full audit pulls in the candidate's GitHub baseline and emits the comparative report:

```bash
byline audit /tmp/sub --candidate some-candidate
```

By default the report prints as Markdown to stdout. Use the output flag to write to a file, or the docx flag to produce a Word artefact instead:

```bash
byline audit /tmp/sub --candidate some-candidate --output ./report.md
open ./report.md
```

For a Word-friendly version, replace the output path argument with a `docx` argument naming a `.docx` destination, then open the resulting file. Both flag names follow the long form documented in `byline audit --help`.

## 10. Common follow-ups

Once the audit is working, three subcommands cover most of what reviewers reach for next:

- `byline align ./sub` runs a deterministic cross-check that the README's documented flags, env vars, commands, and dependencies actually exist in the code.
- `byline questions` generates grounded interview questions from the audit findings. Pass the submission path and the candidate username, and an optional count. Requires the `[llm]` extra and `ANTHROPIC_API_KEY`.
- `byline chat` opens a conversational REPL over the audit so you can ask follow-up questions about specific findings. Same arguments as `questions`, and the same LLM requirements.

## 11. Troubleshooting

A short list of symptoms and remedies, ordered by how often they come up.

**`command not found: byline`.** The user-scripts bin directory is not on `PATH`. Use `python3.12 -m byline ...` as a fallback, or follow step 4 to fix `PATH`.

**`GitHub API rate limit exceeded`.** The anonymous quota was hit. Set `GITHUB_TOKEN` per step 5.

**`ImportError: anthropic` when running questions or chat.** The base install is in use. Reinstall with the LLM extra: `pip install --user 'byline-audit[llm]'`.

**`typo_rate` is always 0.0 with a Java warning.** Java runtime is missing. Install openjdk per step 7, or ignore the metric. Every other metric still works.

**`ApplyAuditedRepoNotFound` or a URL parsing error.** The audit input must be a local path, not an HTTPS URL, until v0.3. Clone the submission first, then point at the working tree.

**`pip` complains about an externally managed environment.** The system Python is restricted. Use a venv (step 4) or the user-install flag.

## 12. Next steps

For the underlying metrics, fingerprint catalogue, disproportion heuristics, and severity scoring, read [`docs/methodology.md`](methodology.md). For the broader framing, limitations, and ethical-use guidance, return to the project [README](../README.md).
