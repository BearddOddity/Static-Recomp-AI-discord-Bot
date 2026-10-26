# Static Recomp AI Discord Bot

A Discord assistant for the static recompiled games community. It answers questions from the projects' GitHub docs and moderates scam and piracy posts, including scams made only of screenshots.

**New here? Follow [SETUP.md](SETUP.md)**: a step-by-step guide from creating the bot to running it, no programming needed.

> AI coding assistants: read [AGENTS.md](AGENTS.md) before changing anything.

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

## Moderation

The bot checks every message from members (anyone with **Manage Messages** is skipped) for scams, piracy, hate speech and breaking your server's rules. Edited messages are checked again.

**Free speech:** opinions, criticism, rudeness, profanity, dark humour and heated arguments are never flagged, even if your rules channel asks for civility. The only speech that gets moderated is **hate speech** (attacks on people for their race, religion, sexuality, gender identity, disability and similar, or threats of violence). Hate speech always times the member out until a moderator reviews the case. It's caught two ways: Discord's built-in **AutoMod Slurs filter** (free and instant, turn it on as described in [SETUP.md](SETUP.md)), and the AI when it reviews a message.

Detection works in two layers:

1. **Built-in rules (free and instant).** These look for:
   - known scam domains, such as the fake MrBeast casino `kegwin.com`
   - lookalike domains (`dlscord-gift.com`, `steamcommunlty.com`), link shorteners, punycode and raw-IP links
   - scam phrasing: crypto giveaways, promo codes, "withdrawal success", free Nitro or Steam gifts, "I accidentally reported you", "DM me to earn", fake urgency, `@everyone`
   - new accounts posting links, and the same message posted in 3+ channels within 2 minutes
2. **AI check (text and images).** When a post looks risky, or has 2+ images, the bot sends the text and images to the AI. That catches screenshot-only scams like those in [`examples/scam/`](examples/scam): a fake verified celebrity post, a casino bonus code page, a "Withdrawal Success" popup and a wallet receipt. The same images posted again reuse the first answer, with no second AI call.

### Warnings, timeouts and bans

The bot warns first and never bans by itself:

1. **Warning.** Each violation opens a **case** and gives the member a warning, which they get by DM (warning 1 of 3, and so on).
2. **Timeout until reviewed.** At `MOD_WARN_LIMIT` active warnings (3 by default), the member is timed out until a moderator looks at the case. **Hate speech** and near-certain scams (a known scam site, or the AI at least 90% sure) go straight here on the first offence. Scams like that usually come from hacked accounts. Discord caps timeouts at 28 days, so the bot renews them; nobody is released until a moderator decides.
3. **Ban.** Only a moderator can ban, using the button on the case report, and the bot asks them to confirm first.

Warnings expire after `MOD_WARN_EXPIRY_DAYS` (90 by default). Moderators can also use `/warn @member reason` (it counts toward the same limit) and `/warnings @member` to see someone's record. Admins can run `/status` to check the bot's setup.

If a member breaks several rules in a short time, for example a hacked account cross-posting a scam to five channels, everything goes into **one case**. That case escalates, and moderators don't get flooded with reports.

### Case reports

Each case is posted to `MOD_LOG_CHANNEL_ID` with the message, the reasons, the links and the images as evidence. It has buttons:

| Button | What it does | Who can use it |
| --- | --- | --- |
| **Ban** | Asks to confirm, then bans and removes their messages from the last 24 hours | Ban Members |
| **Timeout until reviewed** | Times the member out until someone decides | Moderate Members |
| **Lift timeout, keep warning** | Ends the timeout and closes the case; the warning still counts | Moderate Members |
| **Not a violation** | Removes the warning, lifts any timeout and closes the case | Moderate Members |
| **Delete message** | Deletes the flagged message | Manage Messages |

The report updates to show who decided what. Buttons keep working after the bot restarts, because cases are stored in SQLite at `MOD_DB_PATH`.

By default the flagged message stays up (`MOD_ACTION=flag`) and moderators delete it from the report. Once you trust the results, set `MOD_ACTION=delete` so scams disappear immediately.

### Following your rules channel

The bot reads your rules channel (`RULES_CHANNEL_ID`, otherwise the server's Community rules channel, otherwise the first channel with "rules" in its name). It reads plain messages and embeds, and re-reads them whenever a message there is posted, edited or deleted. The rules are then used:

- **in moderation:** the AI is told to enforce them, and a clear break becomes a "Rule violation" case quoting the rule.
- **in `/ask`:** answers follow the rules, for example declining to help find pirated game files.

In the default `MOD_AI_SCAN=suspicious` mode, the AI only sees posts that already look risky, so it mainly catches scams and piracy. To have every message checked against every rule, use `MOD_AI_SCAN=all`. That costs one AI call per message.

**Image scams need a model that can see images.** Claude models can. On OpenRouter, pick a model whose input includes images, and set it as `MOD_AI_MODEL` if your `/ask` model is text-only. Without one, the bot judges only the text and links, and image-only scams get through.

**Bot permissions needed:** View Channels, Send Messages, Embed Links, Attach Files, Read Message History (including in the rules channel), **Manage Messages**, **Moderate Members**, **Ban Members** and **Manage Server** (to see AutoMod). The invite link in [SETUP.md](SETUP.md) grants exactly these. The bot's role must be above the roles of the members it moderates, or Discord will refuse its timeouts and bans.

**Test without Discord:**

```bash
python -m bot.moderation.check examples/scam/*.webp           # uses your AI settings
python -m bot.moderation.check --text "free nitro at dlscord-gift.com"
python -m bot.moderation.check --rules-only --text "..."       # no AI key needed
```

To block a new scam site for everyone, add it to `bot/moderation/data/blocked_domains.txt`. For just your server, add it to `MOD_BLOCKED_DOMAINS`.

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
  main.py            Discord client, /ask, /reload-docs, /warn, /warnings, /status, events
  config.py          Reads settings from the environment
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
    __init__.py      build_provider() picks one from AI_PROVIDER
tests/
examples/scam/       Real scam screenshots for tests and manual checks
start.sh, start.bat  One-click start (sets up Python on first run)
Dockerfile, docker-compose.yml
```

Adding a backend means writing one class with `async complete(messages) -> str` and a branch in `bot/ai/__init__.py`.
