# Static Recomp AI Discord Bot

A Discord assistant for the static recompiled games community. It answers questions with an AI model and is being built to moderate piracy and scam posts.

It connects to Discord, answers `/ask` questions using your community's GitHub docs, and lets you choose the AI backend.

## Quickstart with OpenRouter (free, nothing to host)

1. Make an account at https://openrouter.ai and create a key at https://openrouter.ai/keys.
2. Pick a free model at https://openrouter.ai/models?max_price=0. Free model IDs end in `:free`.
3. In `.env`:

   ```
   AI_PROVIDER=openrouter
   OPENAI_COMPAT_API_KEY=sk-or-...
   OPENAI_COMPAT_MODEL=<model id ending in :free>
   ```

Free OpenRouter models have per-minute and daily request limits. Adding a small credit balance raises them. The bot sends `X-Title` (and `HTTP-Referer` when `OPENROUTER_SITE_URL` is set) so OpenRouter can credit your app.

## Answering from GitHub docs

List the repos to learn from in `DOCS_REPOS`, comma separated, as `owner/repo[@branch][:folder]`. Add `.wiki` to a repo name to read its GitHub wiki instead:

```
DOCS_REPOS=sp00nznet/xboxrecomp,sp00nznet/ps3recomp,rexglue/rexglue-sdk,rexglue/rexglue-sdk.wiki
```

Wikis are read with `git clone`, so `git` must be installed on the machine running the bot. A wiki's pages count as part of its repo for keyword routing.

### Keyword routing

When a question names a platform, `DOCS_ROUTES` makes the bot answer only from that project's docs, so a PS3 question never gets Xbox instructions:

```
DOCS_ROUTES=xbox 360,xbox360,x360,360=rexglue/rexglue-sdk; xbox=sp00nznet/xboxrecomp; ps3=sp00nznet/ps3recomp
```

Each rule is `keywords=owner/repo`, and rules are separated by `;`. Keywords match whole words and ignore case. Rules are checked in order, and a matched keyword is removed before later rules run. That is why "Xbox 360" goes to rexglue and is not also counted as plain "Xbox". A question naming two platforms searches both. A question with no keyword searches every repo. Each route's repo must also be listed in `DOCS_REPOS`.

At startup, and every `DOCS_REFRESH_MINUTES` after, the bot downloads Markdown, reStructuredText and text files from those repos and splits them into sections by heading. For each `/ask`, it finds the most relevant sections, gives them to the AI with the question, and links the source files under the answer. Server managers can run `/reload-docs` after updating the docs.

- Without `GITHUB_TOKEN`, GitHub allows 60 API requests an hour, which covers a few public repos. A read-only fine-grained token raises that and allows private repos.
- Only documentation files are read, not source code. Files over 200 KB, build files such as `CMakeLists.txt`, and folders like `third_party/`, `tests/` and `LICENSES/` are skipped.
- The search is keyword based. It works best when docs use the same words people ask with.

## Choosing an AI backend

Set `AI_PROVIDER` in `.env`. You never need to run a model server for the hosted options.

| `AI_PROVIDER` | What it is | Cost | Set up |
| --- | --- | --- | --- |
| `anthropic` | Claude via the Anthropic API | Paid | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `openrouter` | Many models, including free `:free` ones | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `groq` | Fast hosted open models | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `gemini` | Google Gemini | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `openai_compatible` | Any other OpenAI-compatible endpoint | Varies | Also `OPENAI_COMPAT_BASE_URL` |
| `ollama` | A model on the bot's own machine | Free, needs hardware | `OLLAMA_HOST`, `OLLAMA_MODEL` |
| `cli` | A command-line AI tool (Kilo, OpenCode, etc.) | Depends on the tool | `AI_CLI_COMMAND` |

Free tiers have rate and daily limits, and the list of free models changes over time. Check each provider's current pricing page before relying on one.

For `cli`, the bot runs `AI_CLI_COMMAND` without a shell. It writes the conversation to stdin and reads the reply from stdout. Quote any arguments that contain spaces. Test the command in a terminal first.

> **Warning for `cli`:** coding agents such as Kilo or OpenCode can read files and run commands. With `cli`, anyone in your server can send that agent a prompt. Run it in a non-interactive, read-only mode, ideally in a sandbox or container. Never run it in a folder that holds secrets.

## Setup

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
pytest
```

The provider tests use mocked HTTP responses, so they need no API keys.

## Layout

```
bot/
  main.py            Discord client, /ask and /reload-docs
  config.py          Reads settings from the environment
  docs/
    github.py        Downloads doc files from GitHub repos and wikis
    routing.py       Keyword routing (DOCS_ROUTES)
    index.py         Splits docs into sections and searches them (BM25)
  ai/
    base.py          AIProvider interface and Message type
    anthropic_provider.py
    openai_compat.py   OpenRouter, Groq, Gemini and any OpenAI-compatible API
    ollama.py
    cli.py
    __init__.py      build_provider() picks one from AI_PROVIDER
tests/
```

Adding a backend means writing one class with `async complete(messages) -> str` and a branch in `bot/ai/__init__.py`.
