# Setup guide

This takes about 20 minutes and no programming. You need:

- **Admin** rights on your Discord server.
- **A computer that stays on** to run the bot: your PC while you test, then a small always-on machine or rented server (see step 8).
- **A free AI key** from OpenRouter (step 4). No AI runs on your computer.

---

## 1. Create the bot in Discord

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it (for example "Recomp Helper") and accept the terms.
2. On **General Information**, copy the **Application ID**. You need it in step 2.
3. Open the **Bot** page:
   - Click **Reset Token**, then **Copy**. This is your `DISCORD_TOKEN`. Keep it secret: anyone with it controls your bot. If it ever leaks, reset it again.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent** and click **Save Changes**. Without it the bot can't read messages and won't start.

## 2. Invite the bot to your server

Paste this link into your browser, replacing `YOUR_APPLICATION_ID` with the number from step 1:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=1374389660708
```

Pick your server and click **Authorize**. This grants exactly the permissions the bot uses:

| Permission | Why |
| --- | --- |
| View Channels, Read Message History, Send Messages (and in threads), Embed Links, Attach Files | Read chat, answer `/ask`, post case reports with evidence |
| Manage Messages | Delete scam messages (only if you turn that on) |
| Moderate Members | Time members out while a moderator reviews their case |
| Ban Members | Ban, but **only** when a moderator presses Ban on a case |
| Manage Server | See Discord's AutoMod slur filter, used for hate speech |

The bot also prints this link (with your ID filled in) every time it starts.

## 3. Prepare your server

1. **Turn on Developer Mode** to copy IDs: User Settings → Advanced → **Developer Mode**. You can then right-click any channel or server and choose **Copy ID**.
2. **Make a private `#mod-log` channel** that only moderators and the bot can see. Case reports and their buttons go there. Copy its ID.
3. **Check your rules channel.** The bot reads it and follows it. Copy its ID, or leave it out and the bot finds it by itself.
4. **Turn on the slur filter:** Server Settings → **Safety Setup** → **AutoMod** → the commonly flagged words rule. Enable **Slurs**. For free speech, leave Profanity off. The bot treats a slur-filter hit as hate speech: the member is timed out and a case is opened for review.
5. **Move the bot's role up:** Server Settings → **Roles**, then drag the bot's role above your normal member roles. Discord won't let a bot time out anyone whose role is above its own.

## 4. Get a free AI key

1. Sign up at <https://openrouter.ai> and create a key at <https://openrouter.ai/keys>. It starts with `sk-or-`.
2. Choose **two models** at <https://openrouter.ai/models>:
   - **For `/ask`:** any model whose ID ends in `:free`.
   - **For moderation:** a model that accepts **image** input, so the bot can read scam screenshots. Use the model list's input filter to show image models; free ones also end in `:free`.

Free models have daily limits. A few dollars of credit raises them a lot. You can also use Claude instead (paid, very good with images): set `AI_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

## 5. Download the bot

On the [GitHub page](https://github.com/BearddOddity/Static-Recomp-AI-discord-Bot), click **Code → Download ZIP** and unzip it somewhere permanent. If you have Git installed, you can run `git clone https://github.com/BearddOddity/Static-Recomp-AI-discord-Bot` instead.

## 6. Fill in the settings

Copy `.env.example` to a new file called `.env`. The start scripts in step 7 do this for you on the first run. Open `.env` in any text editor (Notepad works) and fill in:

```
DISCORD_TOKEN=          # from step 1
DISCORD_GUILD_ID=       # your server's ID (right-click server icon → Copy ID)

AI_PROVIDER=openrouter
OPENAI_COMPAT_API_KEY=  # sk-or-... from step 4
OPENAI_COMPAT_MODEL=    # your :free model for /ask
MOD_AI_MODEL=           # your image-capable model for moderation

MOD_LOG_CHANNEL_ID=     # your #mod-log channel ID
RULES_CHANNEL_ID=       # optional: your rules channel ID
```

Everything else already has sensible defaults, explained in the comments in `.env`. Never share `.env` or upload it anywhere.

## 7. Start it

**Windows**
1. Install Python from <https://www.python.org/downloads/>. On the first installer screen, tick **"Add python.exe to PATH"**.
2. Install Git from <https://git-scm.com/download/win>. The bot uses it to read the ReXGlue wiki.
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
- `/ask question: how do I install the ReXGlue SDK?`
- From a **non-moderator** account (moderators are never moderated), post `free nitro at dlscord-gift.com`. A case report should appear in `#mod-log` with buttons.

## 9. Keep it running

The bot is only online while its computer is on and the window or container is running. For 24/7, use any always-on machine: a spare PC, a Raspberry Pi, or a small rented Linux server (VPS) with Docker. Use `docker compose up -d` there; it restarts by itself after reboots.

Cases and warnings are stored in the `data/` folder. Keep that folder when you move or update the bot.

**Updating:** download the new version (or run `git pull`), keep your `.env` and `data/`, and start it again.

## 10. After the first week

The bot starts out cautious. It warns members and reports cases, but leaves flagged messages up for a moderator to delete. Once the reports look right, open `.env` and set:

```
MOD_ACTION=delete          # remove scam messages immediately
MOD_HATE_ACTION=delete     # remove hate speech immediately
```

Bans always stay a moderator's decision.

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
| ReXGlue wiki not loading | Install Git (step 7) |
