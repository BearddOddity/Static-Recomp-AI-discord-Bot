# Development

Running from source, tests, and how the code is laid out. AI coding assistants should also read [AGENTS.md](../AGENTS.md).

## Running from source

Requires Python 3.10 or newer.

1. Create a Discord application and bot at https://discord.com/developers/applications. Copy the bot token. Under **Bot**, turn on **Message Content Intent**, or the bot will fail to log in. Invite the bot with the `bot` and `applications.commands` scopes.
2. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` and your chosen provider.
3. Install and run:

   ```bash
   python -m venv .venv
   . .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python -m bot.main
   ```

4. In Discord, run `/ask question:...`.

Set `DISCORD_GUILD_ID` to your server ID while testing so commands update right away.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check bot tests
pytest
```

The tests use mocked HTTP and Discord objects, so they need no API keys or network. GitHub runs them on every push ([`.github/workflows/tests.yml`](../.github/workflows/tests.yml)).

## Layout

```
bot/
  main.py            Discord client, /ask, /reload-docs, /warn, /warnings, /status, events
  assist.py          Finding doc excerpts, follow-up memory, splitting long answers
  health.py          AI problems -> #mod-log notice + GitHub pull request or issue
  updater.py         Self-update from GitHub, then restart
  config.py          Reads settings from the environment, .env and SERVER_PROFILE
  docs/
    github.py        Downloads doc files from GitHub repos and wikis
    routing.py       Keyword routing (DOCS_ROUTES)
  moderation/        Scam and piracy detection (see AGENTS.md for how it decides)
    index.py         Splits docs into sections and searches them (BM25)
  ai/
    base.py          AIProvider interface and Message type
    anthropic_provider.py
    openai_compat.py   OpenRouter, Groq, Gemini and any OpenAI-compatible API
    ollama.py
    cli.py
    fallback.py      Backup models and free OpenRouter models
    __init__.py      build_provider() picks one from AI_PROVIDER
tests/
servers/static-recomp/  One community's profile (delete it in your own copy)
examples/scam/       Real scam screenshots for tests and manual checks
start.sh, start.bat  One-click start; restarts after updates and crashes
deploy/              Start at boot: install-linux.sh (systemd), install-windows.bat
Dockerfile, docker-compose.yml
```

Adding a backend means writing one class with `async complete(messages) -> str` and a branch in `bot/ai/__init__.py`.

[← Back to the README](../README.md)
