# Xenon Support Bot

An AI-assisted Discord support bot grounded in the official [Xenon documentation](https://wiki.xenon.bot). It uses OpenAI's Responses API with `gpt-5.6-luna`, strict function tools, and structured outputs to keep answers fast, inexpensive, and traceable to pages the model actually read.

## What it does

- Searches and reads official Xenon documentation on demand.
- Rejects unrelated questions and fails closed when the docs do not establish an answer.
- Adds citation buttons only for documentation pages actually retrieved during the run.
- Supports screenshots, follow-up questions, live progress, rate limiting, and community escalation.
- Stores server configuration, documentation, full-text search data, and analytics in PostgreSQL.
- Exposes administration, analytics, documentation refresh, stats, and setup commands in Discord.

## Architecture

```text
Discord interaction
      │
      ▼
bounded AgentRunner ─── GPT-5.6 Luna / Responses API
      │                         │
      ├── search_docs ◄─────────┤
      └── get_doc ◄─────────────┘
              │
              ▼
        PostgreSQL FTS
```

The model adapter is behind a small `ModelClient` interface. The agent loop owns tool budgets, continuation items, structured answer validation, and citation allowlisting. PostgreSQL is the canonical document store and search engine, so scraping and retrieval no longer depend on a separate local index.

## Quick start with Docker

Requirements: Docker Compose, a Discord bot token, and an OpenAI API key with access to `gpt-5.6-luna`.

```bash
git clone https://github.com/LoggeL/xenon-support-bot.git
cd xenon-support-bot
cp .env.example .env
# Fill in DISCORD_TOKEN, OPENAI_API_KEY, and POSTGRES_PASSWORD.
docker compose up --build -d
```

Run `/scrape` once in Discord, then `/setup-support-menu` to publish the support entry point.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | yes | — | Discord bot token |
| `OPENAI_API_KEY` | yes | — | OpenAI API key |
| `DATABASE_URL` | yes | — | PostgreSQL connection URL; Compose supplies its own |
| `POSTGRES_PASSWORD` | Compose | — | Password for the bundled PostgreSQL container |
| `OPENAI_MODEL` | no | `gpt-5.6-luna` | Pinned and validated model ID |
| `OPENAI_REASONING_EFFORT` | no | `medium` | `none`, `low`, `medium`, `high`, `xhigh`, or `max` |
| `OPENAI_MAX_OUTPUT_TOKENS` | no | `1800` | Per-response output ceiling |
| `RATE_LIMIT_PER_MINUTE` | no | `5` | Per-user request limit |
| `ADMIN_USER_IDS` | no | project owner | Comma-separated bot-owner IDs |
| `LOG_LEVEL` | no | `INFO` | Application log level |

Secrets are validated at startup and are never written to application logs.

## Discord commands

| Command | Access | Purpose |
| --- | --- | --- |
| `/setup-support-menu` | Manage Server | Publish the support menu and configure escalation |
| `/scrape` | Bot owner or guild admin | Refresh official documentation |
| `/support-config show` | Manage Server | Show server configuration |
| `/support-analytics` | Manage Server | Show recent support metrics |
| `/support-unanswered` | Manage Server | Show unresolved questions |
| `/stats` | Everyone | Show bot usage and uptime |
| `/about` | Everyone | Show product information |

## Local development

The project uses Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python -m src.main
```

CI runs formatting, linting, type checking, tests, and a package build on every pull request.

## Operational notes

- The agent is capped at eight documentation calls across six model turns.
- Function tools are strict and read-only.
- Model-proposed source slugs are checked against successful `get_doc` results before links are shown.
- Schema initialization and the JSONB-to-full-text migration are idempotent.
- The Docker container runs as an unprivileged user.

## License

MIT
