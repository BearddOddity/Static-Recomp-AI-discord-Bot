# Setup guide

This sets up **your own** bot: your own Discord application, keys and data, separate from every other copy. It takes about 20 minutes and no programming. You need:

- **Admin** rights on your Discord server.
- **A computer that stays on** to run the bot: your PC while you test, then a small always-on machine or rented server (see step 8).
- **A free AI key** from OpenRouter (step 4). No AI runs on your computer.

---

## 1. Create the bot in Discord

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it (for example "Community Helper") and accept the terms.
2. On **General Information**, copy the **Application ID**. You need it in step 2.
3. Open the **Bot** page:
   - Click **Reset Token**, then **Copy**. This is your `DISCORD_TOKEN`. Keep it secret: anyone with it controls your bot. If it ever leaks, reset it again.
   - Turn **off Public Bot**, so only you can invite it. The bot only works in your own server and leaves any other server it's added to, but turning this off stops strangers trying.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent** and click **Save Changes**. Without it the bot can't read messages and won't start. If you want a welcome message for new members, also turn on **Server Members Intent** here ([details](docs/COMMUNITY.md#welcome-message)).

## 2. Invite the bot to your server

Paste this link into your browser, replacing `YOUR_APPLICATION_ID` with the number from step 1:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=1460289006692
```

Pick your server and click **Authorize**. This grants exactly the permissions the bot uses:

| Permission | Why |
| --- | --- |
| View Channels, Read Message History, Send Messages (and in threads), Embed Links, Attach Files, Add Reactions | Read chat, answer `/ask`, post case reports with evidence |
| Manage Messages | Delete scam, piracy and rule-breaking messages when it warns someone |
| Moderate Members | Time members out while a moderator reviews their case |
| Ban Members | Ban, but **only** when a moderator presses Ban on a case |
| Manage Server | See Discord's AutoMod slur filter, used for hate speech |
| Create Private Threads, Manage Threads | `/help` threads: a private thread per member, closed when they're done |

The bot also prints this link (with your ID filled in) every time it starts.

Never give it more, especially **Administrator**. The full list, what never to give it, and what your staff need is on one page: [docs/PERMISSIONS.md](docs/PERMISSIONS.md).

## 3. Prepare your server

1. **Turn on Developer Mode** to copy IDs: User Settings → Advanced → **Developer Mode**. You can then right-click any channel or server and choose **Copy ID**.
2. **Make a private `#mod-log` channel** that only moderators and the bot can see. Case reports and their buttons go there. Copy its ID.
3. **Check your rules channel.** The bot reads it and follows it. Copy its ID, or leave it out and the bot finds it by itself.
4. **Turn on the slur filter:** Server Settings → **Safety Setup** → **AutoMod** → the commonly flagged words rule. Enable **Slurs**. For free speech, leave Profanity off. The bot treats a slur-filter hit as hate speech: the member is timed out and a case is opened for review.
5. **Place the bot's role:** Server Settings → **Roles**, then drag the bot's role **above your normal member roles and below your staff roles**. Discord won't let a bot act on anyone above it, so this way it can moderate members but can never touch your moderators, even if something goes wrong. Never give it Administrator.
6. **Read [docs/SECURITY.md](docs/SECURITY.md)** once. It's a short checklist (two-factor login, a protected `main` branch, an AI spending limit) that keeps the bot from ever being turned against your server.

## 4. Get a free AI key

1. Sign up at <https://openrouter.ai> and create a key at <https://openrouter.ai/keys>. It starts with `sk-or-`.
2. Choose **two models** at <https://openrouter.ai/models>:
   - **For `/ask`:** any model whose ID ends in `:free`.
   - **For moderation:** a model that accepts **image** input, so the bot can read scam screenshots. Use the model list's input filter to show image models; free ones also end in `:free`.

Free models have daily limits. A few dollars of credit raises them a lot. You can also use Claude instead (paid, very good with images): set `AI_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

## 5. Download the bot

On the bot's GitHub page, click **Code → Download ZIP** and unzip it somewhere permanent. If you have Git installed, you can run `git clone <the page's URL>` instead.

If you're making a bot for a new community from this template, delete the `servers/` folder; it holds another community's settings.

## 6. Fill in the settings

Copy `.env.example` to a new file called `.env`. The start scripts in step 7 do this for you on the first run. Open `.env` in any text editor (Notepad works) and fill in:

```
DISCORD_TOKEN=          # from step 1
DISCORD_GUILD_ID=       # your server's ID (right-click server icon → Copy ID). The bot only works here

AI_PROVIDER=openrouter
OPENAI_COMPAT_API_KEY=  # sk-or-... from step 4
OPENAI_COMPAT_MODEL=    # your :free model for /ask
MOD_AI_MODEL=           # your image-capable model for moderation

MOD_LOG_CHANNEL_ID=     # your #mod-log channel ID
RULES_CHANNEL_ID=       # optional: your rules channel ID

COMMUNITY_ABOUT=        # what the server is about, e.g. "retro handheld modding"
DOCS_REPOS=             # optional: GitHub repos /ask answers from, e.g. owner/repo,owner/repo.wiki
```

If your community already has a profile in the `servers/` folder, set `SERVER_PROFILE=servers/<name>/server.env` instead of `COMMUNITY_ABOUT` and `DOCS_REPOS`.

Everything else already has sensible defaults, explained in the comments in `.env`. Never share `.env` or upload it anywhere.

## 7. Start it

**Windows**
1. Install Python from <https://www.python.org/downloads/>. On the first installer screen, tick **"Add python.exe to PATH"**.
2. Install Git from <https://git-scm.com/download/win>. The bot uses it to read GitHub wikis listed in `DOCS_REPOS`.
3. Double-click **`start.bat`**. The first run takes a minute to set things up.

**Mac / Linux**
1. Install Python 3.10 or newer, and Git. Both come with most Linux distributions. On a Mac, run `xcode-select --install`.
2. In a terminal, in the bot's folder, run `./start.sh`.

**Docker** (good for servers)
```
docker compose up -d
docker compose logs -f     # watch the logs
```

When you see `Logged in as ...`, the bot is online.

## 8. Check everything

In your server, type **`/status`**. It checks the AI, the case report channel, the rules, the docs, the AutoMod slur filter, the bot's permissions and its role position. Fix anything marked ⚠️.

Then try it:
- `/ask question: how do I get started?`, or mention the bot with a question. Reply to its answer to ask a follow-up.
- From a **non-moderator** account (moderators are never moderated), post `free nitro at dlscord-gift.com`. The message should disappear, the account should get a warning DM, and a case report with buttons should appear in `#mod-log`.

## 9. Keep it running

You don't need to keep `start.bat` open on your PC. Put the bot on any always-on machine, ideally a **free cloud server**, and it runs by itself: it starts at boot, restarts after crashes, and **updates itself from GitHub**. [docs/HOSTING.md](docs/HOSTING.md) walks through it, including free options. In short:

- **Linux server or Raspberry Pi:** `git clone` the repo, fill in `.env`, run `./deploy/install-linux.sh`.
- **Docker:** `git clone` the repo, fill in `.env`, run `docker compose up -d`.
- **Your Windows PC:** run `deploy\install-windows.bat` once, and the bot starts whenever you sign in.

Self-updates need a `git clone`, not the ZIP download. Cases and warnings are stored in the `data/` folder; keep it if you move the bot.

**When the AI needs attention:** if a free model disappears, the bot switches to another free one by itself and says so in `#mod-log`. With `HEALTH_GITHUB_REPO` and `HEALTH_GITHUB_TOKEN` set, it also opens a pull request that makes the switch permanent (merge it and the bot updates itself), or an issue if the key needs replacing.

## 10. How it treats members

1. **1st and 2nd warning:** the message is deleted (a copy stays in the case report) and the member gets a DM.
2. **3rd warning:** the member is timed out, and the case waits for a moderator to decide with the buttons.
3. **Hate speech** skips straight to step 2.

Bans are always a moderator's decision. If you'd rather review messages before they're deleted, set `MOD_ACTION=flag` in `.env`. Shared game files and piracy links are deleted either way.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Bot won't start, mentions `PrivilegedIntentsRequired` | Turn on **Message Content Intent** (step 1.3) |
| "Missing required environment variable" or "needs: ..." | That setting is empty in `.env` |
| Slash commands don't show up | Set `DISCORD_GUILD_ID`, or wait up to an hour. Check you invited with the link in step 2 |
| "Could not time out" in case reports | Move the bot's role higher (step 3.5). Server owners and admins can't be timed out |
| No case reports | Check `MOD_LOG_CHANNEL_ID`, and that the bot can see and post in that channel |
| Screenshot scams aren't caught | `MOD_AI_MODEL` must be a model that accepts images. `/status` tells you |
| "The AI provider failed" | Check the key and model name. Free models can hit their daily limit |
| Hate speech from AutoMod isn't opening cases | Turn on the **Slurs** filter (step 3.4). The bot needs **Manage Server** |
| A GitHub wiki isn't loading | Install Git (step 7) |
