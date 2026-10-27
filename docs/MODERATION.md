# Moderation

How the bot catches scams, piracy and hate speech, the warning ladder, and case reports. How strict the AI is (`MOD_MIN_CONFIDENCE`) can be changed live with `/tune`: see [TUNING.md](TUNING.md).


The bot checks every message from members (anyone with **Manage Messages** is skipped) for scams, piracy, hate speech and breaking your server's rules. Edited messages are checked again.

**Free speech:** opinions, criticism, rudeness, profanity, dark humour and heated arguments are never flagged, even if your rules channel asks for civility. The only speech that gets moderated is **hate speech** (attacks on people for their race, religion, sexuality, gender identity, disability and similar, or threats of violence). Hate speech always times the member out until a moderator reviews the case. It's caught two ways: Discord's built-in **AutoMod Slurs filter** (free and instant, turn it on as described in [SETUP.md](../SETUP.md)), and the AI when it reviews a message.

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
2. **AI check (text and images).** When a post looks risky, or has 2+ images, the bot sends the text and images to the AI. That catches screenshot-only scams like those in [`examples/scam/`](../examples/scam): a fake verified celebrity post, a casino bonus code page, a "Withdrawal Success" popup and a wallet receipt. The same images posted again reuse the first answer, with no second AI call.

### Warnings, timeouts and bans

The bot warns first and never bans by itself:

1. **Warning.** Each violation (a scam, piracy, a broken rule) **deletes the message**, opens a **case report** for moderators with a copy of the message and its images, and gives the member a warning by DM (warning 1 of 3, and so on).
2. **Timeout until reviewed.** The **3rd warning** (`MOD_WARN_LIMIT`) times the member out and hands the case to a human. **Hate speech** goes straight here on the first offence. Discord caps timeouts at 28 days, so the bot renews them; nobody is released until a moderator decides. Optionally, `MOD_SEVERE_SCAM_REVIEW=true` sends near-certain scams (usually hacked accounts) here on the first offence too.
3. **Ban.** Only a moderator can ban, using the button on the case report, and the bot asks them to confirm first.

Warnings expire after `MOD_WARN_EXPIRY_DAYS` (90 by default). Moderators can also use `/warn @member reason` (it counts toward the same limit) and `/warnings @member` to see someone's record. Admins can run `/status` to check the bot's setup.

If a member breaks several rules in a short time, for example a hacked account cross-posting a scam to five channels, everything goes into **one case**. That case escalates, and moderators don't get flooded with reports.

### Case reports

Each case is posted to `MOD_LOG_CHANNEL_ID` as a colour-coded card: a status line saying whether anyone needs to act, the member's name and avatar, the message quoted, a warning meter (🟧🟧⬜ 2 of 3), why it was flagged, how sure the AI was, the links (not clickable), a numbered timeline of what happened, and the images as spoilered evidence. When a moderator decides, the card turns grey and shows who decided and when. [See what they look like](case-reports.png) (rendered from the bot's own code). It has buttons:

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

**Bot permissions needed:** View Channels, Send Messages, Embed Links, Attach Files, Read Message History (including in the rules channel), **Manage Messages**, **Moderate Members**, **Ban Members** and **Manage Server** (to see AutoMod). The invite link in [SETUP.md](../SETUP.md) grants exactly these. The bot's role must be above the roles of the members it moderates, or Discord will refuse its timeouts and bans.

**Test without Discord:**

```bash
python -m bot.moderation.check examples/scam/*.webp           # uses your AI settings
python -m bot.moderation.check --text "free nitro at dlscord-gift.com"
python -m bot.moderation.check --rules-only --text "..."       # no AI key needed
```

To block a new scam site for everyone, add it to `bot/moderation/data/blocked_domains.txt`. For just your server, add it to `MOD_BLOCKED_DOMAINS`. Piracy sites work the same way with `piracy_domains.txt` and `MOD_PIRACY_DOMAINS`.

[← Back to the README](../README.md)
