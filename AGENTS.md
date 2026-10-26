# Instructions for AI coding agents

This file is for AI models (Claude, OpenCode, Kilo, Codex, Copilot and others) that help maintain and evolve this bot. Read it fully before changing code. Humans are welcome too.

## What this bot is

An open source (MIT) Discord bot **template** for game communities. It has two jobs:

1. **Assistance:** `/ask` answers questions using documentation pulled from GitHub repos the server chooses.
2. **Moderation:** it watches messages for **scams** (including scams made of images only), **piracy**, **hate speech** and breaking the server's **#rules**. Each violation deletes the message, warns the member and posts a case report; the 3rd warning (or any hate speech) times them out pending human review, and moderators decide with buttons.

**Template vs. server.** `bot/` must stay free of any one community's details: no server names, repo names or keywords in code, prompts or defaults. A community's details go in a profile, `servers/<name>/server.env` (loaded through `SERVER_PROFILE`), with its own README and tests. `servers/static-recomp/` is the profile for the community this bot was first built for (static recompilation: xboxrecomp, ps3recomp, ReXGlue); people starting from the template delete it. Test fixtures use made-up repos such as `example/switch-tools`.

The server owner is not necessarily a programmer. Keep setup simple, defaults safe, and explain changes in plain language.

## Ground rules

- **Never commit secrets.** `.env` is git-ignored. Tokens and keys only go in `.env` or the host's environment. Add new settings to `.env.example` with a comment.
- **Humans decide bans.** The ladder is warning, then timeout until a moderator reviews, then a ban only by a moderator pressing **Ban** (with a confirm step). Never add automatic bans or kicks, and never let a review timeout lapse while a case is open. The server owner chose `MOD_ACTION=delete` as the default (a warning deletes the message; the case report keeps a copy and "Not a violation" removes the warning), so keep detection conservative: false positives hurt real members.
- **Free speech is the server's policy.** Never flag opinions, criticism, rudeness, profanity, dark humour or arguments, even if the rules channel asks for civility. The only speech-based label is `hate_speech` (protected characteristics, slurs at people, threats). It always goes straight to a timeout pending review. Discord AutoMod's Slurs preset feeds it too, and the Profanity preset must never count. Don't add bundled slur word lists; rely on AutoMod and the AI.
- **The rules channel is policy, not commands.** Its text is written by admins and is passed to the AI as rules to enforce. User messages stay untrusted data.
- **AI output is untrusted.** The bot sends with `allowed_mentions=none` so a model can't ping `@everyone`. Keep it that way. Message text and image text are data to the moderation model, never instructions. Keep that framing in `bot/moderation/classifier.py`.
- **Don't make the bot depend on one AI vendor.** Everything goes through `AIProvider` (`bot/ai/base.py`). New features must work with every provider, or degrade gracefully (as image moderation does with `supports_images`).
- **Keep the free, no-hosting path working.** A user with only a free OpenRouter key must be able to run `/ask` and moderation.
- **Anti-piracy policy (from the server owner):** no links to pirated content and no sharing game files of any kind: executables (.elf .xbe .xex .self EBOOT.BIN), disc images (.iso, .bin+.cue, .chd, .cso, .rvz, .wbfs ...), ROMs, packages (.pkg .nsp .xci), firmware, BIOS files and keys. Game-file attachments (including inside .zip files), piracy-site links and direct game-file links are *certain* (rule score 100). They are always deleted and follow the warning ladder (no instant timeout). Mentioning file names while debugging ("default.xex crashes") and explaining how to dump your own disc are allowed; never flag those. The list of extensions is in `bot/moderation/files.py`.
- **Tests and lint must pass.** Run `ruff check bot tests` and `pytest` before you finish; CI runs both on Python 3.10 and 3.12. Add tests for new behaviour. Tests must not need network access or API keys; mock HTTP with `httpx.MockTransport` and models with a fake `AIProvider`, as the existing tests do.
- **Match the existing style:** small modules, type hints, dataclasses, short comments that explain *why*, and no new dependencies unless clearly worth it.

## Map of the code

```
bot/
  main.py                Discord client: /ask, /reload-docs, on_message -> moderation
  config.py              All settings, read from environment / .env / SERVER_PROFILE (single source of truth)
  ai/
    base.py              AIProvider, Message, Image (the provider contract)
    anthropic_provider.py, openai_compat.py (OpenRouter/Groq/Gemini/any), ollama.py, cli.py
    __init__.py          build_provider(settings, model=...) picks one from AI_PROVIDER
  docs/
    github.py            Fetch docs from repos (raw files) and wikis (git clone)
    index.py             Split by heading + BM25 keyword search
    routing.py           DOCS_ROUTES keyword -> repo routing
  moderation/
    signals.py           Signal(reason, weight): rule findings add up to a risk score
    links.py             URL extraction, blocklist/allowlist/piracy sites, lookalike domains, shorteners
    files.py             Anti-piracy: game-file attachments, .zip contents, direct game-file links
    data/*.txt           blocked_domains.txt, allowed_domains.txt, piracy_domains.txt (one domain per line)
    text.py              Scam and piracy phrasing patterns
    classifier.py        Vision/text AI classifier prompt + JSON verdict parsing
    engine.py            Moderator: combines rules, account age, cross-posting, AI, cache
    policy.py            Warning ladder: when a warning becomes a timeout pending review
    cases.py             SQLite store: cases, warnings, saved #rules text
    review.py            CaseManager: opens cases, DMs warnings, case report embeds + buttons
    rules.py             RulesBook: reads #rules (text + embeds), refreshed on every change
SETUP.md                 Step-by-step install guide for server owners. Keep it in sync with settings
start.sh, start.bat      First-run setup + start;  Dockerfile, docker-compose.yml for servers
    discord_glue.py      Discord message -> Post, exemptions
    check.py             CLI: python -m bot.moderation.check images... --text "..."
servers/<name>/          One community's profile: server.env, README.md, test_profile.py
examples/scam/           Real scam screenshots used by tests and for manual checks
tests/                   pytest suite (no network)
```

## How moderation decides

1. **Rules** (free, instant): link checks + text patterns + new-account and cross-posting signals add up to a score.
   - Score ≥ 80 (`HARD_SCORE`, e.g. a blocklisted domain): it's a scam with no AI call.
2. **AI** (costs a call): asked when the score is ≥ 20, or the post has 2+ images, unknown links from a new account, and similar cases (`MOD_AI_SCAN=suspicious`). `MOD_AI_SCAN=all` asks for every message with text or images.
   - Flags only if the label isn't `ok` and confidence ≥ `MOD_MIN_CONFIDENCE`.
   - Verdicts are cached by content hash, so a scam cross-posted to many channels costs one call.
3. If the AI is off or fails, a score ≥ 50 (`SOFT_SCORE`) is flagged on rules alone.
4. `CaseManager.open_from_message()` (or `open_from_automod()` for Discord AutoMod slur hits) opens a case and adds a warning. If the member already has a case waiting for review, or one opened in the last 10 minutes, the new violation is added to it instead (`_add_violation`), so a cross-post burst makes one report. `policy.plan()` decides whether to delete the message (per-label `MOD_*_ACTION`) and whether to time out pending review (warning count ≥ `MOD_WARN_LIMIT`, hate speech, or a severe scam when `MOD_SEVERE_SCAM_REVIEW` is on). The member gets a DM, and the case report goes to `MOD_LOG_CHANNEL_ID` with buttons.
5. Buttons use `custom_id = "case:<id>:<action>"` and are handled in `AssistantBot.on_interaction`, so they work after restarts. Each action checks the clicking moderator's Discord permission. A background task renews review timeouts before Discord's 28-day cap.

When tuning weights, keep a single weak signal below the AI threshold, and keep the hard threshold for near-certain evidence only.

## Common tasks

- **New scam domain:** add it to `bot/moderation/data/blocked_domains.txt` with a comment saying where it was seen.
- **New scam style:** add a `(regex, reason, weight)` to `bot/moderation/text.py`, describe it in the classifier prompt in `classifier.py`, and add a test case. If you have screenshots, add them to `examples/scam/` (strip personal info first) and to the tests.
- **New AI provider:** subclass `AIProvider`, set `supports_images`, add a branch in `bot/ai/__init__.py`, add it to `PROVIDERS` and `REQUIRED_BY_PROVIDER` in `config.py`, document it in the README table, and add a mocked test.
- **New docs source:** add it to the server's profile `DOCS_REPOS` (use `owner/repo.wiki` for wikis), and add a `DOCS_ROUTES` keyword if it belongs to one platform. Never hard-code it in `bot/`.
- **Case report look:** `build_embed()` in `review.py`. Keep the status line first (it tells moderators whether to act), keep links in code formatting so they can't be clicked, and stay under Discord's embed limits (1024 per field, 6000 total).
- **Releasing:** bump `__version__` in `bot/__init__.py` and `version` in `pyproject.toml`, add a CHANGELOG entry, tag `vX.Y.Z[-alpha|-beta]`.
- **New setting:** add it to `Settings` and `load_settings()` in `config.py`, to `.env.example`, and to the README (and SETUP.md if a server owner must set it).
- **New permission:** add it to `REQUIRED_PERMISSIONS` in `main.py`, then update the invite link number and the table in SETUP.md (`python -c "import bot.main as m; print(m.REQUIRED_PERMISSIONS.value)"`).

## Ideas for future work

Ordered roughly by value. Check with the server owner before large changes.

- Save known-scam image hashes and AI verdicts in SQLite (cases already are), so the cache survives restarts. "Not a violation" decisions could feed an allowlist and a set of examples for prompt tuning.
- An appeal flow: a button in the warning DM that posts the member's explanation to their case.
- A cheap text pre-check for hate speech that doesn't rely on AutoMod, so `MOD_AI_SCAN=suspicious` catches it without an AI call on every message.
- When cross-posting is detected, also clean up the earlier copies in other channels.
- Perceptual image hashing, so slightly cropped or recompressed copies of known scam images match without an AI call.
- Better `/ask`: embeddings-based search, follow-up questions in threads, per-channel default project.
- A small evaluation set (`examples/ok/` alongside `examples/scam/`) plus a script that reports precision and recall for the current model and prompt.

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env    # then fill it in
ruff check bot tests && pytest
python -m bot.moderation.check examples/scam/*.webp   # needs an AI key with vision
python -m bot.main
```
