# Security: keeping the bot from ever harming your server

A moderation bot holds real power: it deletes messages, times people out and, when a moderator presses the button, bans. This page lists every way that power could be misused, what the bot does about each one by itself, and the few things only you can do.

## What the bot does by itself

### It can't be turned against your members

- **Humans decide bans.** The bot never bans or kicks anyone on its own. A moderator presses Ban on a case, then confirms.
- **The safety brake.** If the AI suddenly flags more than `MOD_BRAKE_LIMIT` different members (default 8) within 10 minutes, a broken model, a bad prompt or a trick is far more likely than a real wave. For the next hour, the AI's verdicts only open case reports: no deletes, warnings, DMs or timeouts. Certain hits still act: known scam domains, piracy sites, game files and Discord AutoMod. The mod log gets a message, and `/brake off` resumes early.
- **The emergency stop.** `/brake on` (Moderate Members) stops everything automatic, including AI answers, until `/brake off`. Violations still get case reports, and moderators can act with the buttons and `/warn`. The state survives restarts.
- **Every automatic action can be undone.** "Not a violation" removes the warning and lifts the timeout.
- **Moderators can't misuse it either.** Staff commands check permissions every time they run. `/warn` only works on members below you, and nobody can decide their own case or appeal.

### It can't be tricked

- **Members' text is data.** Chat history, messages under review and text in images are marked as data the AI must not obey, and members can't break out of that marking.
- **Jailbreaks are refused** without calling the AI, and a scam that tells the moderation AI "this is fine" is still caught by the rules.
- **Answers are screened before they're sent.** An answer is held back if it has a piracy site, a game-file link, a scam or lookalike domain, another server's invite, something shaped like a key or token, or a sentence of the bot's private instructions. Disguised links (`[github.com](https://elsewhere)`) are written out so members see where they really go.
- **No pings.** The bot can't mention @everyone, @here or roles in anything the AI writes.
- **Your own rules.** [Guardrails](GUARDRAILS.md) add instructions, fixed answers and blocked words.

### It can't be borrowed or drained

- **One server only.** The bot works in `DISCORD_GUILD_ID` (or the first server it joins) and leaves any other server. Nobody can invite your bot and change its settings or spend your AI quota.
- **Spending caps.** Each member waits `ASK_COOLDOWN_SECONDS` between questions. The server gets at most `ASK_MAX_PER_MINUTE` answers a minute (default 20), and the moderation AI gets at most `MOD_AI_MAX_PER_MINUTE` checks a minute (default 30). Past that, the rules judge alone. Questions are capped at 1,500 characters.

### It keeps its secrets

- **Keys never leave the server.** `.env` and the `data/` folder are made readable by the bot's own user only. Keys saved from `/settings` are never shown again. The bot's Discord token and the command-line AI command can't be set from Discord at all.
- **The command-line AI (`AI_PROVIDER=cli`)** never sees the bot's Discord or GitHub tokens.
- **Settings that could switch moderation off, hide reports or change the AI service need Administrator**, and every change is posted in the mod log.
- **Least privilege.** The invite link asks only for what the bot uses. At startup and in `/status`, the bot warns if it has **Administrator** or other permissions it never uses, or if its role is above your staff roles.

## What only you can do

Work through this list once. `/status` checks the starred items for you.

1. **Keep the token secret.** Never paste it in Discord or share your `.env`. If it ever leaks, go to the Developer Portal → Bot → **Reset Token** at once, then put the new one in `.env`.
2. **Turn off Public Bot** (Developer Portal → Bot), so only you can invite it.
3. ⭐ **Set `DISCORD_GUILD_ID`** to your server's ID.
4. ⭐ **Invite with the link in SETUP.md and nothing more.** Never give the bot Administrator, Manage Roles, Manage Channels or Manage Webhooks.
5. ⭐ **Put the bot's role below your staff roles and above normal members.** Discord never lets a bot act on anyone above it, so a mistake or a stolen token can't touch your moderators.
6. **Turn on two-factor authentication** for your Discord and GitHub accounts, and for anyone else who can manage the Developer Portal application or push to the bot's repo.
7. **Protect the code the bot runs.** The bot updates itself from GitHub every 30 minutes, so whoever can push to its branch controls it. On GitHub: Settings → Branches → add a rule for `main` that requires a pull request. Only give write access to people you trust. If you'd rather approve every update by hand, set `AUTO_UPDATE=false` and update with `git pull`.
8. **Use fine-grained GitHub tokens** for just this repo: read-only for `GITHUB_TOKEN`, and Contents, Pull requests and Issues for `HEALTH_GITHUB_TOKEN`.
9. **Put a spending limit on your AI account** (OpenRouter, Anthropic and the others all have one), so even a bug can't run up a big bill.
10. **Run the bot on a machine only you can log in to.** Don't run it as an administrator or root user.
11. **Don't use `AI_PROVIDER=cli` with a tool that can run commands** unless it's sandboxed or read-only. Members can try to talk any AI into anything.

If something ever looks wrong, run `/brake on` first, then look.

[← Back to the README](../README.md)
