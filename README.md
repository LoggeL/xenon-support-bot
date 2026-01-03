# 🤖 Xenon Support Bot

An intelligent Discord support bot for [Xenon](https://xenon.bot) that uses **agentic RAG** (Retrieval-Augmented Generation) to answer questions based on official documentation.

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![Discord.py](https://img.shields.io/badge/discord.py-2.3+-5865F2?logo=discord&logoColor=white)
![OpenRouter](https://img.shields.io/badge/OpenRouter-GPT--5.1-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🧠 **Agentic RAG** | Uses function calling to search and retrieve docs on-demand |
| 🎯 **Relevance Filter** | Silently ignores questions unrelated to Xenon |
| ⚡ **Live Progress** | Shows real-time tool steps as the agent works |
| 🔍 **Full-Text Search** | Whoosh-powered search across all doc sections |
| 🖼️ **Image Support** | Analyzes screenshots attached to questions |
| 💬 **Context Memory** | Remembers the last 5 messages per channel |
| ⏱️ **Rate Limiting** | Configurable per-user request limits |
| 📋 **Discord Embeds** | Clean, formatted responses with length handling |

---

## 🔄 How It Works

```
User Question
     │
     ▼
┌─────────────────────────┐
│  🤔 Check Relevance     │  ← Is this about Xenon?
└─────────────────────────┘
     │
   Yes ──► Continue
   No  ──► Silent (no response)
     │
     ▼
┌─────────────────────────┐
│  🔍 Search/Read Docs    │  ← Agent calls tools one-by-one
└─────────────────────────┘
     │
     ▼
┌─────────────────────────┐
│  📖 Generate Answer     │  ← Based on retrieved docs only
└─────────────────────────┘
     │
     ▼
   Discord Embed Response
```

The agent sees a list of available documentation pages but must **call tools** to read content. This ensures answers are grounded in actual documentation.

---

## 🛠️ Agent Tools

| Tool | Description |
|------|-------------|
| `check_relevance` | Determines if the question is about Xenon |
| `search_docs` | Full-text search across all documentation |
| `get_doc` | Retrieves full content of a specific doc page |

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Discord Bot Token → [Create one here](https://discord.com/developers/applications)
- OpenRouter API Key → [Get one here](https://openrouter.ai)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/xenon-support-bot.git
cd xenon-support-bot

# Configure environment
cp .env.example .env
nano .env  # Edit with your credentials

# Deploy
docker compose up -d
```

### Initialize Documentation

In Discord, run `/scrape` (admin only) to fetch the latest Xenon docs.

---

## ⚙️ Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Your Discord bot token |
| `OPENROUTER_API_KEY` | Your OpenRouter API key |
| `DISCORD_CHANNEL_ID` | Channel ID where bot listens |
| `ADMIN_USER_IDS` | Comma-separated admin user IDs |

### Optional Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_MODEL` | `openai/gpt-5.1` | LLM model for responses |
| `RATE_LIMIT_PER_MINUTE` | `5` | Max requests per user per minute |

---

## 💬 Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/scrape` | Scrape latest Xenon documentation | Admin only |
| `/clear` | Clear conversation history for channel | Everyone |

---

## 🧑‍💻 Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run locally
python -m src.main

# Scrape docs manually
python -m src.docs.scraper

# Rebuild search index
python -c "from src.docs.search import doc_search; doc_search.rebuild_index()"
```

---

## 📁 Project Structure

```
xenon-support-bot/
├── src/
│   ├── main.py              # Entry point
│   ├── config.py            # Environment settings
│   ├── bot.py               # Discord bot, embeds, rate limiting
│   ├── agent/
│   │   ├── client.py        # OpenRouter API client
│   │   ├── runner.py        # Agentic loop (sequential tools)
│   │   └── tools.py         # Tool definitions & execution
│   └── docs/
│       ├── scraper.py       # Wiki scraper for wiki.xenon.bot
│       ├── store.py         # Document storage & retrieval
│       └── search.py        # Whoosh full-text search
├── data/                    # Scraped docs & search index
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## 🧰 Tech Stack

- **Python 3.11+** — Runtime
- **discord.py** — Discord API wrapper
- **OpenRouter** — LLM API with function calling
- **Whoosh** — Pure Python full-text search
- **httpx** — Async HTTP client
- **BeautifulSoup** — HTML parsing for scraper
- **Pydantic** — Settings and validation

---

## 📄 License

MIT

---

## 🙏 Credits

- [Xenon Bot](https://xenon.bot) — The Discord backup bot this supports
- [OpenRouter](https://openrouter.ai) — LLM API provider
