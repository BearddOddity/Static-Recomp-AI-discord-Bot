# Tuning: moderation confidence and chat context

Two settings change how the bot behaves more than any others. Moderators can change both from Discord with `/tune`, without editing files or restarting the bot.

| Setting | Default | Range | Changes |
| --- | --- | --- | --- |
| `MOD_MIN_CONFIDENCE` | `0.75` | `0.50` to `0.99` | How sure the moderation AI must be before it flags a post |
| `CHAT_CONTEXT_MESSAGES` | `8` | `0` to `20` | How many recent channel messages the assistant reads before it answers |

## Changing them from Discord

Anyone with the **Moderate Members** permission can use `/tune`. Other members don't see the command.

- `/tune` with nothing filled in shows the current values.
- `/tune confidence:0.85` changes only the moderation confidence.
- `/tune context:3` changes only the chat context.
- You can set both in one command.

What happens when you change a value:

- It applies to the very next message. The bot does not restart.
- It is saved in `data/tuning.json`, so it survives restarts and self-updates. The value in `.env` is only the starting value for a bot that has never been tuned. After the first `/tune`, the saved value wins.
- The bot posts one line in your mod-log channel saying who changed what, from which value to which value, so every change has a record.
- Numbers outside the range are refused by Discord. If a value somehow gets through, the bot clamps it to the range.

To go back to the `.env` values, delete `data/tuning.json` on the server and restart the bot.

## Moderation confidence (`MOD_MIN_CONFIDENCE`)

### What it is

When the built-in rules can't decide about a message, the bot asks the AI to judge it. The AI answers with a verdict (scam, piracy, hate speech, rule-breaking, or fine) and a confidence from 0 to 1, which is how sure it is. The bot only acts when the confidence is **at least** this setting.

This setting does not touch the built-in rules. Known scam domains, piracy sites, game files and the AutoMod slur filter work without the AI and ignore the number, so they still catch what they always caught.

### The trade-off

| Setting | Effect | Cost |
| --- | --- | --- |
| Lower (0.50 to 0.70) | Catches more scams and rule-breaking | More innocent messages flagged. Moderators spend time dismissing cases, and members get warned for nothing. |
| `0.75` | Balanced starting point | |
| Higher (0.80 to 0.99) | Fewer false alarms | More real problems slip through, especially clever scams and image-only scams. |

### How to pick a value

1. Start with `0.75` and let the bot run for about a week.
2. Look at the cases in your mod-log channel.
   - Moderators keep dismissing cases as wrong: raise it by 0.05 to 0.10, to `0.85`.
   - Scams or piracy get through and members report them: lower it by 0.05 to 0.10, to `0.65`.
3. Change one step at a time and wait a few days before changing it again.

Free AI models are less reliable than paid ones, and their confidence numbers are rough. Because of that, don't go below `0.65` unless you have a moderator reviewing every case. Don't go above `0.90` for long: at that level the AI rarely flags anything.

### Related settings

- `MOD_AI_SCAN` decides which messages are sent to the AI at all (`off`, `suspicious`, or `all`). Confidence only applies to messages that were sent.
- `MOD_ACTION`, `MOD_PIRACY_ACTION` and `MOD_RULE_ACTION` decide what happens to a flagged message (`delete` or `flag`). With `flag`, a low threshold is much cheaper, because nothing is deleted until a moderator agrees.
- `MOD_SEVERE_CONFIDENCE` (default `0.9`) is a separate, higher bar used only when `MOD_SEVERE_SCAM_REVIEW=true`, to send obvious scams straight to a timeout pending review.
- The bot never bans on its own. Bans stay a moderator's button press on the case report.

## Chat context (`CHAT_CONTEXT_MESSAGES`)

### What it is

When someone asks the bot a question, it first reads the last few messages in that channel. That's how "what do you think of Sam's idea?" makes sense to it, and how a short follow-up like "and on Linux?" works even in a busy channel.

### What it costs

- **Privacy.** Those messages are sent to your AI provider along with the question. Members who didn't ask the bot anything have their messages sent too. If your community cares about that, keep this low or at `0`.
- **Off-topic remarks.** The bot may bring up something said in the channel earlier. That's expected with a high value.
- **Quota.** Each extra message makes the request bigger. On free AI tiers that can mean slower answers and more rate limiting.

Only messages with text are read, each cut to 300 characters, and the total is capped at about 1,500 characters. The bot treats them as background and not as instructions.

### How to pick a value

| Value | Use it when |
| --- | --- |
| `0` | You want no channel messages sent to the AI. The bot answers only the question itself, plus the conversation it is in when someone replies to one of its answers. |
| `3` | A good balance: short back-and-forths work, and little of the chat leaves the server. |
| `8` (default) | Busy channels where people refer to what was just said. |
| `12` to `20` | Rarely worth it. The context is capped at about 1,500 characters anyway, so the extra messages are mostly cut off. |

Replying directly to one of the bot's answers always continues that conversation, whatever this setting is. The setting only controls how much of the surrounding channel the bot sees.

## Where each value comes from

From lowest to highest priority:

1. The default in the code (`0.75` and `8`).
2. The server profile in `SERVER_PROFILE`.
3. `.env`.
4. `data/tuning.json`, written by `/tune` or by the same two settings on the [settings page](SETTINGS.md).

## Related commands

- `/status` shows whether moderation is on, which AI models are in use and what is missing.
- `/ai-mode` (Manage Server) switches which models answer `/ask`.
- `/warn` and `/warnings` (Moderate Members) work on members directly.
