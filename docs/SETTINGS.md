# The settings page

`/settings` opens a private page in Discord where moderators change the bot's setup without editing `.env` or logging in to the server: channels, AI provider and models, API keys, moderation and assistant behaviour.

## Who can use it

| Permission | Can do |
| --- | --- |
| **Manage Server** | Open the page and change everything that isn't marked 🔒 |
| **Administrator** (or the server owner) | Also change the 🔒 settings: keys and tokens, the AI provider, the custom API address, the personality, and the docs repositories |

The page is **private**: it's an "only you can see this" message, and its menus and buttons answer only to the person who opened it. The bot checks the permission again on every click and every form, because a server can change who sees a command under Server Settings → Integrations.

The 🔒 settings need more trust because they decide where your data and API keys go. Someone who could point the AI at their own address would receive your members' messages, and someone who could change the docs repositories could change what the bot treats as the source of truth.

## How to use it

1. Type `/settings`.
2. Pick a group: **Channels**, **AI**, **Keys**, **Moderation**, **Assistant** or **Community**.
3. Pick a setting. A form opens for text and numbers; a menu appears for on/off and fixed choices.
4. Save. The page shows what changed.
5. Press **Restart now** when it says a restart is needed. The bot is back in about 20 seconds.

Changes are saved straight away, so you can change several settings and restart once. Settings waiting for a restart show ⏳.

Two settings apply at once with no restart: **Confidence needed** and **Chat context**. They are the same two that `/tune` changes. See [TUNING.md](TUNING.md).

## What can be changed

**Channels:** mod log channel, rules channel, assistant channels, server ID.

**AI:** provider, models for `/ask`, models for moderation, free-model fallback (OpenRouter), backup provider and its models, custom API address, `/ai-mode` modes.

**Keys** (🔒): the main AI key, Anthropic key, backup AI key, GitHub token for docs, GitHub token for health pull requests, and the health repository.

**Moderation:** moderation on or off, AI scan mode, the delete-or-flag action for scams, piracy, rule-breaking and hate speech, warnings before a timeout, how long warnings last, DM on warning, whether warned members can appeal, exempt roles, and the confidence needed.

**Community:** welcome channel and message, help threads on or off, help channel, and the staff role pinged by "Get a human" ([details](COMMUNITY.md)).

**Assistant:** community topic, personality (🔒), chat context, cooldown, answer mentions, docs repositories and routes (🔒), docs sections per answer, AI docs search, and self-update.

### What can't be changed here, on purpose

- **The bot's Discord token** (`DISCORD_TOKEN`). A wrong token would stop the bot from logging in, with no way to fix it from Discord. Change it in `.env` on the server.
- **The command-line AI** (`AI_CLI_COMMAND`). It runs a command on the server, so it stays in `.env`.
- **Bans.** The bot never bans on its own. A moderator presses Ban on a case report.

## Keys and privacy

- A key is **never shown** after you save it. The page says only "✅ set" or "not set". The mod-log message says "value hidden".
- Pasting a key into a Discord form sends it through Discord's servers. It's safer than a public channel, but if you'd rather it never touch Discord, set keys in `.env` on the server.
- Keys are stored in `data/settings.json` on the server, readable only by the bot's user. That's the same level of protection as `.env`.
- Every change posts a line in your mod-log channel saying who changed what. For keys it says the value is hidden.
- If a key leaks, replace it with the provider and enter the new one here.

## Where the values live, and which wins

From lowest to highest priority:

1. The code's defaults.
2. The server profile (`SERVER_PROFILE`).
3. `.env`.
4. `data/settings.json`, written by `/settings`.
5. `data/tuning.json`, written by `/tune` or the live settings here.

So **a value saved from Discord beats `.env`**. If you later edit `.env` and nothing changes, a saved setting is overriding it. Running `push-bot-env.ps1` replaces `.env` but doesn't touch `data/settings.json`.

To give a setting back to `.env`, delete `data/settings.json` on the server and restart, or press **Reset all Discord settings** on the home page (administrators only; it asks you to click twice).

## If a saved setting breaks the bot

The bot checks saved settings when it starts. If they stop it from starting (an unknown provider, a number out of range, a name that doesn't exist), it:

1. moves the file to `data/settings.rejected.json`,
2. starts normally from `.env`, and
3. posts a warning in the mod log saying why.

So a typo made in Discord can't lock you out. Open `data/settings.rejected.json` to see what was wrong.

A setting that is valid but wrong, such as a key the provider rejects, doesn't stop the bot. The AI health check reports it in the mod log, and you can fix it from the same page.

## Troubleshooting

- **`/settings` doesn't appear.** Slash commands take up to an hour to show on a bot that has no `DISCORD_GUILD_ID` set. Set the server ID (Channels → Server ID) and restart.
- **"This settings page isn't yours to use."** Another member opened that page. Run `/settings` for your own.
- **"Changing that needs the Administrator permission."** You have Manage Server but not Administrator, and the setting is marked 🔒.
- **I changed a setting and nothing happened.** Look for ⏳. It applies after **Restart now**.
- **The page stopped responding.** Pages expire after 10 minutes. Run `/settings` again.
