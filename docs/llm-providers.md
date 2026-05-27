# LLM providers

`byline` supports three backend families behind a single `LLMProvider` abstraction: Anthropic Claude, OpenAI GPT, and any OpenAI-compatible self-hosted endpoint (Ollama, vLLM, LM Studio, llama.cpp). Selection happens entirely through environment variables; there are no new CLI flags to learn. Every provider's output flows through the same banned-phrase post-processor, so the framing rules described in `docs/methodology.md` apply uniformly regardless of which backend is configured.

## Overview

The four LLM-touching surfaces in `byline` are the qualitative summary on `audit`, the optional semantic pass on `align --with-llm`, the `questions` interview-question generator, and the interactive `chat` REPL. All four resolve their provider once at startup via `byline.llm_provider.get_llm_provider()` and hold that handle for the duration of the run. The base install remains LLM-free; both SDKs only load when `[llm]` is installed and an LLM-touching subcommand is invoked.

## Provider selection

The env-var contract is small enough to keep in your head:

```
BYLINE_LLM_PROVIDER  default: anthropic           accepted: anthropic | openai
BYLINE_LLM_MODEL     default: provider-default    optional override
ANTHROPIC_API_KEY    required when provider is anthropic
OPENAI_API_KEY       required when provider is openai
OPENAI_BASE_URL      optional; redirects openai calls to a self-hosted endpoint
```

`byline` only reads the API key for the provider you actually selected. If you set `BYLINE_LLM_PROVIDER=openai` without `OPENAI_API_KEY`, the error message will name `OPENAI_API_KEY`, not `ANTHROPIC_API_KEY`.

## Anthropic (Claude)

The default. No env vars needed beyond the API key:

```bash
export BYLINE_LLM_PROVIDER=anthropic    # optional, this is the default
export ANTHROPIC_API_KEY=sk-ant-...
# Optional: pin a specific Claude model
export BYLINE_LLM_MODEL=claude-sonnet-4-5
```

Verify with a small interview-question run:

```bash
byline questions tests/fixtures/synthetic_ai_repo -n 3
```

You should see three questions emitted as JSON-grounded entries with cited findings.

## OpenAI (GPT)

```bash
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-proj-...
export BYLINE_LLM_MODEL=gpt-4o          # or gpt-4o-mini, gpt-4-turbo, etc
```

The same `byline questions` smoke test confirms the wiring.

## Self-hosted via Ollama

Three steps: install Ollama, pull a model, point `byline` at the OpenAI-compatible endpoint Ollama exposes on port 11434.

```bash
# 1. Install (macOS / Linux)
brew install ollama        # or curl https://ollama.com/install.sh | sh
ollama serve &
ollama pull llama3.2

# 2. Point byline at Ollama (uses the OpenAI-compatible endpoint at port 11434)
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=ollama            # any non-empty string; Ollama ignores it
export OPENAI_BASE_URL=http://localhost:11434/v1
export BYLINE_LLM_MODEL=llama3.2
```

From here, every LLM subcommand routes through the local Ollama server. There is no network call to OpenAI; the official `openai` Python SDK is being used purely as a thin HTTP client.

## Self-hosted via vLLM, LM Studio, or llama.cpp

Same shape as Ollama — point `OPENAI_BASE_URL` at the local server, set `BYLINE_LLM_MODEL` to the name your backend recognises, and set `OPENAI_API_KEY` to any non-empty string. Examples:

```bash
# vLLM
vllm serve meta-llama/Llama-3.2-3B-Instruct &
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=local
export OPENAI_BASE_URL=http://localhost:8000/v1
export BYLINE_LLM_MODEL=meta-llama/Llama-3.2-3B-Instruct

# LM Studio (defaults to port 1234)
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=local
export OPENAI_BASE_URL=http://localhost:1234/v1
export BYLINE_LLM_MODEL=<the model name LM Studio reports>

# llama.cpp server
export BYLINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=local
export OPENAI_BASE_URL=http://localhost:8080/v1
export BYLINE_LLM_MODEL=<the model name llama.cpp reports>
```

## Caveats with self-hosted models

Smaller local models do not always behave the way frontier-class hosted models do. The most common things to watch out for:

- They may ignore the JSON-array output instruction. Both `align --with-llm` and `questions` parse JSON; malformed output is logged and skipped (the audit degrades gracefully to deterministic-only).
- They may drift from the framing rules. The banned-phrase post-processor catches the most common slips, but a model with strong priors toward detection language may produce more rewrites than a frontier model would. Pin a larger local model if you find the rewrites are eating substantive content.
- They run slower. CPU-only inference of a 7B-class model can take tens of seconds per call; an audit that issues several LLM calls may take minutes.

A reasonable smoke test for any self-hosted setup is `byline align <repo> --with-llm`, since alignment is the cheapest of the LLM-touching commands and degrades gracefully when the model output is unusable.

## Troubleshooting

- **`OPENAI_API_KEY not set`** — set it, even for self-hosted. The `openai` client requires a non-empty string. Use `OPENAI_API_KEY=local` (or any placeholder) when pointing at Ollama / vLLM / LM Studio.
- **`Connection refused`** — the local server is not running, or the port in `OPENAI_BASE_URL` does not match what the server is listening on. Verify with `curl $OPENAI_BASE_URL/models`.
- **`Unknown model`** — the value of `BYLINE_LLM_MODEL` must match exactly what your backend recognises. Run `ollama list` (for Ollama) or check your server's `/v1/models` endpoint.
- **`ANTHROPIC_API_KEY not set` while `BYLINE_LLM_PROVIDER=openai`** — you exported the wrong env var; `byline` only reads the key for the configured provider. Set `OPENAI_API_KEY` instead.
- **The audit ran but the qualitative section is blank** — the model returned output that did not parse. Re-run with `BYLINE_LLM_DEBUG=1` (or check the logs) to see the raw response.

## Framing-rule note

Every provider's output passes through `strip_banned_phrases` before reaching the user. The configured banned set lives in `byline.llm.BANNED_PHRASES`. This is defense in depth on top of the per-provider system prompts: even if a backend drifts toward detection language, the post-processor will scrub the worst offenders before the user sees them.

## Switching mid-session

You can flip providers between subcommand runs by changing the environment. Within one subcommand invocation, the provider is resolved once at startup and held for the duration; there is no mid-run switching. If you want to compare provider behaviour on the same input, run the subcommand twice with different `BYLINE_LLM_PROVIDER` values and diff the reports.
