# github-issue-triage

Point it at any public GitHub repository and get a structured triage brief: top priorities with reasoning and suggested actions, stale issues to close, quick wins, and likely duplicates — all in one LLM call.

## Setup

```bash
git clone https://github.com/VidipKhattar/github-issue-triage
cd github-issue-triage
python -m venv .venv && source .venv/bin/activate
cp .env.example .env          # add your API key(s) and optional GitHub token
pip install -e .
```

> Each new terminal session: run `source .venv/bin/activate` before using `triage`.

## Usage

```bash
# Analyse issues from the last 30 days
triage psf/requests --since 30

# Large active repo — last 7 days only
triage microsoft/vscode --since 7

# Focus on specific concerns
triage pallets/flask --since 14 --focus "security and auth issues"

triage langchain-ai/langchain --since 14 --focus "security and auth issues"

# Preview without calling the LLM
triage pydantic/pydantic --since 30 --dry-run

# JSON output
triage astral-sh/ruff --since 30 --output json

# Use a different provider
triage psf/requests --since 14 --provider gpt
triage psf/requests --since 14 --provider gemini
```

> **Tip:** Use `--since` to control scope naturally.
> A busy repo like `vscode` generates hundreds of issues per week —
> `--since 7` keeps analysis focused and cost under $0.10.

## Flags

| Flag                  | Default  | Description                                                                                              |
| --------------------- | -------- | -------------------------------------------------------------------------------------------------------- |
| `--since`             | `7`      | Only analyse issues created in the last N days. Recommended: `7` for large repos, `30` for smaller ones. |
| `--focus` / `-f`      | —        | Plain-English priority directive injected into the LLM prompt                                            |
| `--provider` / `-p`   | `claude` | LLM provider: `claude`, `gpt`, or `gemini`                                                               |
| `--dry-run`           | `false`  | Skip the LLM call; print token and cost estimates instead                                                |
| `--output` / `-o`     | `table`  | `table` (Rich) or `json`                                                                                 |
| `--stale-days` / `-s` | `90`     | Days without update before flagging stale                                                                |

## Configuration

| Variable            | Required                | Description                                     |
| ------------------- | ----------------------- | ----------------------------------------------- |
| `DEFAULT_PROVIDER`  | No (default: `claude`)  | Active provider when `--provider` is not passed |
| `MODEL_CLAUDE`      | No (default: see below) | Anthropic model used when provider is `claude`  |
| `MODEL_OPENAI`      | No (default: see below) | OpenAI model used when provider is `gpt`        |
| `MODEL_GEMINI`      | No (default: see below) | Google model used when provider is `gemini`     |
| `ANTHROPIC_API_KEY` | Yes (if using `claude`) | Your Anthropic API key                          |
| `OPENAI_API_KEY`    | Yes (if using `gpt`)    | Your OpenAI API key                             |
| `GEMINI_API_KEY`    | Yes (if using `gemini`) | Your Google AI Studio API key                   |
| `GITHUB_TOKEN`      | Recommended             | PAT — raises rate limit from 60 to 5,000 req/hr |

### Switching providers

Pass `--provider` at runtime — no `.env` changes needed:

```bash
triage psf/requests --since 14 --provider claude
triage psf/requests --since 14 --provider gpt
triage psf/requests --since 14 --provider gemini
```

To change the default, set `DEFAULT_PROVIDER` in `.env`. To change which specific model a provider uses, set the corresponding variable:

```
# Defaults — override in .env to change the model without touching code
MODEL_CLAUDE=claude-sonnet-4-20250514   # or claude-opus-4-20250514
MODEL_OPENAI=gpt-4o                     # or gpt-4o-mini
MODEL_GEMINI=gemini/gemini-1.5-pro      # or gemini/gemini-2.0-flash
```

All routing goes through [LiteLLM](https://docs.litellm.ai) — the provider flag simply resolves to the appropriate model string.

### Tuning scope via `.env`

Two variables directly control how many issues are analysed per run:

```
# Only look at issues from the last N days (default: 7)
SINCE_DEFAULT=7

# Hard cap on issues fetched from GitHub (default: 500)
MAX_ISSUES=500
```

For a large active repo like `microsoft/vscode`, `SINCE_DEFAULT=7` + `MAX_ISSUES=100` keeps each run under $0.06. For a quieter repo, `SINCE_DEFAULT=30` gives a fuller picture for roughly the same cost.

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the full design note covering architectural
decisions, trade-offs, and what was deliberately not built.

```
src/triage/
├── cli.py          # Typer entry point — flags, argument parsing
├── config.py       # Settings loaded from .env
├── github.py       # GitHub API client — paginated, rate-limit aware
├── preprocessor.py # HTML stripping, signal extraction, since/stale filters
├── llm.py          # LiteLLM wrapper, retry, token logging
├── models.py       # Pydantic models — TriageReport, IssuePriority, etc.
├── pipeline.py     # Orchestrator — progress output, dry-run, cost estimates
├── reporter.py     # Rich table and JSON renderers
└── prompts/
    ├── system.md   # Main system prompt (schema injected at runtime)
    └── focus.md    # Focus directive template
```

The pipeline makes exactly **one LLM call** per run. Cost is bounded by `--since`
and visible before spending via `--dry-run`.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v        # unit tests (no API keys required)
```
