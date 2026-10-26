# AI Discord Bot

An open source Discord assistant and moderator for game communities. It answers questions from your community's GitHub docs and moderates scams, piracy and hate speech, including scams made only of screenshots. It works with Claude, free OpenRouter models, or a model on your own machine.

**New here? Follow [SETUP.md](SETUP.md)**: a step-by-step guide from creating the bot to running it, no programming needed.

> AI coding assistants: read [AGENTS.md](AGENTS.md) before changing anything.

## Use it as a template

The code in `bot/` holds nothing specific to any one server. Everything about a particular community (its topic, docs repos and keywords) lives in a **server profile**, so you can start your own bot from this one:

1. Copy the repository: on GitHub, use **Use this template** (if the owner has turned on *Template repository* in Settings) or **Fork**, or download the ZIP.
2. Delete the `servers/static-recomp/` folder. It's the profile for the community this bot was first built for, and isn't needed by the template.
3. Set `COMMUNITY_ABOUT`, `DOCS_REPOS` and `DOCS_ROUTES` in `.env`, or make your own `servers/<name>/server.env` and point `SERVER_PROFILE` at it.

It's MIT licensed (see [LICENSE](LICENSE)): use it, change it and build on it, for any purpose. Keep the license notice.

### Built-in profile: Static Recomp

This repository also runs the Static Recomp community's bot. Its settings are in [`servers/static-recomp/`](servers/static-recomp/), and its owner turns them on with `SERVER_PROFILE=servers/static-recomp/server.env`.

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

## The assistant

The bot is a general AI assistant, not just a docs search. Its personality is inspired by Tony Stark's J.A.R.V.I.S.: calm, quick, precise, polite, with a dry wit, and it answers the question first, then offers what you'll probably need next. It helps with anything: your community's projects, programming and debugging, games, hardware, general knowledge, writing or just conversation. Your GitHub docs are its reference: it uses and cites them when they're relevant, and answers from its own knowledge when they aren't.

- **Ask it:** `/ask`, mention it (`@Bot why does my build fail?`), or reply to one of its answers to follow up. It remembers the last few turns, so "and on Windows?" works.
- **Assistant channels:** list channel IDs in `ASSISTANT_CHANNEL_IDS` and it answers every message there, like a chat window.
- **It knows where it is:** who's asking, the server and channel, today's date, and the last few messages in the channel (`CHAT_CONTEXT_MESSAGES`), so "what do you think of Sam's idea?" makes sense to it.
- **Screenshots:** attach an image (an error dialog, a crash log, a settings screen) and ask about it. This needs a model that accepts images.
- **Its own rules still apply:** it follows the server's rules channel and the anti-piracy policy, and it says when it isn't sure instead of making things up. It can't browse the web.
- **Personality:** set `AI_SYSTEM_PROMPT` to replace it. `{name}`, `{server}` and `{community}` are filled in.

Long answers arrive in up to three messages instead of being cut off. `ASK_ON_MENTION=false` turns off mentions and replies (`/ask` and assistant channels still work).

## Answering from GitHub docs

List the repos to learn from in `DOCS_REPOS`, comma separated, as `owner/repo[@branch][:folder]`. Add `.wiki` to a repo name to read its GitHub wiki instead:

```
DOCS_REPOS=owner/switch-tools,owner/switch-tools.wiki,owner/ps3-tools@main:docs
```

Wikis are read with `git clone`, so `git` must be installed on the machine running the bot. A wiki's pages count as part of its repo for keyword routing.

### Keyword routing

When a question names a platform, `DOCS_ROUTES` makes the bot answer only from that project's docs, so a PS3 question never gets Switch instructions:

```
DOCS_ROUTES=switch 2,switch2=owner/switch2-tools; switch=owner/switch-tools; ps3=owner/ps3-tools
```

Each rule is `keywords=owner/repo`, and rules are separated by `;`. Keywords match whole words and ignore case. Rules are checked in order, and a matched keyword is removed before later rules run. That is why "Switch 2" goes to the first repo and is not also counted as plain "Switch". A question naming two platforms searches both. A question with no keyword searches every repo. Each route's repo must also be listed in `DOCS_REPOS`.

At startup, and every `DOCS_REFRESH_MINUTES` after, the bot downloads Markdown, reStructuredText and text files from those repos and splits them into sections by heading. For each question, it finds the most relevant sections, gives them to the AI as reference, and links the source files under the answer. Questions that have nothing to do with the docs (small talk, other subjects) get no excerpts, so the AI answers freely. Server managers can run `/reload-docs` after updating the docs.

**How the search finds the right section:**

- Each section knows the headings above it ("Installing > Linux"), so a short subsection is still found by its topic.
- Sections are split at paragraph breaks, and code blocks are never cut in half.
- Word forms match ("configured", "configuration"), and so do common synonyms ("setup" and "install", "crash" and "freeze") and small typos ("recompilr").
- Words that appear side by side in the docs ("save files") rank higher, and no single file can fill every slot.
- If the question's words barely match the docs, the AI first rephrases it into the words the docs use, then searches again (`DOCS_AI_QUERY=fallback`, the default). `always` does this for every question, for one extra AI call each; `off` never does.

- Without `GITHUB_TOKEN`, GitHub allows 60 API requests an hour, which a shared server can use up. When that happens, the bot reads public repos with `git clone` instead. A read-only fine-grained token raises the limit and allows private repos.
- Only documentation files are read, not source code. Files over 200 KB, build files such as `CMakeLists.txt`, and folders like `third_party/`, `tests/` and `LICENSES/` are skipped.

## Moderation

The bot checks every message from members (anyone with **Manage Messages** is skipped) for scams, piracy, hate speech and breaking your server's rules. Edited messages are checked again.

**Free speech:** opinions, criticism, rudeness, profanity, dark humour and heated arguments are never flagged, even if your rules channel asks for civility. The only speech that gets moderated is **hate speech** (attacks on people for their race, religion, sexuality, gender identity, disability and similar, or threats of violence). Hate speech always times the member out until a moderator reviews the case. It's caught two ways: Discord's built-in **AutoMod Slurs filter** (free and instant, turn it on as described in [SETUP.md](SETUP.md)), and the AI when it reviews a message.

**Links are welcome.** YouTube, GitHub, any GIF, and any wiki (Wikipedia, Fandom, wiki.gg, project wikis) are always safe, and every other link is fine too. A link only counts against someone if it's a known scam or piracy site, imitates a brand (`dlscord-gift.com`), or points straight at a game file. Links from brand-new accounts, or with odd signs like a shortener, get a quick AI second look, but are never flagged just for being a link.

**Anti-piracy policy:** no links to pirated content, and no sharing game files of any kind. That covers console executables (`.elf`, `.xbe`, `.xex`, `.self`, `EBOOT.BIN`), disc images (`.iso`, `.bin`+`.cue`, `.chd`, `.cso`, `.rvz`, `.wbfs` and more), ROMs, console packages (`.pkg`, `.nsp`, `.xci`) and license keys. These are recognised by the rules, with no AI guesswork:

- **game files attached** to a message, including ones hidden inside a `.zip`
- **links to piracy sites** (list in `bot/moderation/data/piracy_domains.txt`, plus `MOD_PIRACY_DOMAINS`)
- **direct links to game files** on file hosts or the Discord CDN (`…/Halo3.iso`); trusted sites such as GitHub releases are skipped

Those messages are **always deleted**, and the member gets a warning on the normal ladder. Piracy is often a newcomer who didn't know, so it's 3 warnings before a timeout pending review, and bans stay with moderators. Things the AI only suspects, such as someone asking where to find an ISO, are deleted too unless `MOD_PIRACY_ACTION=flag`. Talking about files is fine: "my default.xex crashes on startup" or "how do I dump my own disc?" are never flagged. `/ask` refuses to help find game files and points people to dumping their own.

Detection works in two layers:

1. **Built-in rules (free and instant).** These look for:
   - known scam domains, such as the fake MrBeast casino `kegwin.com`
   - lookalike domains (`dlscord-gift.com`, `steamcommunlty.com`), link shorteners, punycode and raw-IP links
   - scam phrasing: crypto giveaways, promo codes, "withdrawal success", free Nitro or Steam gifts, "I accidentally reported you", "DM me to earn", fake urgency, `@everyone`
   - new accounts posting links, and the same message posted in 3+ channels within 2 minutes
2. **AI check (text and images).** When a post looks risky, or has 2+ images, the bot sends the text and images to the AI. That catches screenshot-only scams like those in [`examples/scam/`](examples/scam): a fake verified celebrity post, a casino bonus code page, a "Withdrawal Success" popup and a wallet receipt. The same images posted again reuse the first answer, with no second AI call.

### Warnings, timeouts and bans

The bot warns first and never bans by itself:

1. **Warning.** Each violation (a scam, piracy, a broken rule) **deletes the message**, opens a **case report** for moderators with a copy of the message and its images, and gives the member a warning by DM (warning 1 of 3, and so on).
2. **Timeout until reviewed.** The **3rd warning** (`MOD_WARN_LIMIT`) times the member out and hands the case to a human. **Hate speech** goes straight here on the first offence. Discord caps timeouts at 28 days, so the bot renews them; nobody is released until a moderator decides. Optionally, `MOD_SEVERE_SCAM_REVIEW=true` sends near-certain scams (usually hacked accounts) here on the first offence too.
3. **Ban.** Only a moderator can ban, using the button on the case report, and the bot asks them to confirm first.

Warnings expire after `MOD_WARN_EXPIRY_DAYS` (90 by default). Moderators can also use `/warn @member reason` (it counts toward the same limit) and `/warnings @member` to see someone's record. Admins can run `/status` to check the bot's setup.

If a member breaks several rules in a short time, for example a hacked account cross-posting a scam to five channels, everything goes into **one case**. That case escalates, and moderators don't get flooded with reports.

### Case reports

Each case is posted to `MOD_LOG_CHANNEL_ID` as a colour-coded card: a status line saying whether anyone needs to act, the member's name and avatar, the message quoted, a warning meter (🟧🟧⬜ 2 of 3), why it was flagged, how sure the AI was, the links (not clickable), a numbered timeline of what happened, and the images as spoilered evidence. When a moderator decides, the card turns grey and shows who decided and when. [See what they look like](docs/case-reports.png) (rendered from the bot's own code). It has buttons:

| Button | What it does | Who can use it |
| --- | --- | --- |
| **Ban** | Asks to confirm, then bans and removes their messages from the last 24 hours | Ban Members |
| **Timeout until reviewed** | Times the member out until someone decides | Moderate Members |
| **Lift timeout, keep warning** | Ends the timeout and closes the case; the warning still counts | Moderate Members |
| **Not a violation** | Removes the warning, lifts any timeout and closes the case | Moderate Members |
| **Delete message** | Deletes the flagged message | Manage Messages |

The report updates to show who decided what. Buttons keep working after the bot restarts, because cases are stored in SQLite at `MOD_DB_PATH`.

Flagged messages are deleted right away and kept in the report. To leave them up for a moderator to judge instead, set `MOD_ACTION=flag` (and `MOD_PIRACY_ACTION`, `MOD_RULE_ACTION`, `MOD_HATE_ACTION`); the report then has a **Delete message** button.

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

To block a new scam site for everyone, add it to `bot/moderation/data/blocked_domains.txt`. For just your server, add it to `MOD_BLOCKED_DOMAINS`. Piracy sites work the same way with `piracy_domains.txt` and `MOD_PIRACY_DOMAINS`.

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
pip install -r requirements-dev.txt
ruff check bot tests
pytest
```

The tests use mocked HTTP and Discord objects, so they need no API keys or network. GitHub runs them on every push (`.github/workflows/tests.yml`).

## Layout

```
bot/
  main.py            Discord client, /ask, /reload-docs, /warn, /warnings, /status, events
  assist.py          Finding doc excerpts, follow-up memory, splitting long answers
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
    __init__.py      build_provider() picks one from AI_PROVIDER
tests/
servers/static-recomp/  One community's profile (delete it in your own copy)
examples/scam/       Real scam screenshots for tests and manual checks
start.sh, start.bat  One-click start (sets up Python on first run)
Dockerfile, docker-compose.yml
```

Adding a backend means writing one class with `async complete(messages) -> str` and a branch in `bot/ai/__init__.py`.
