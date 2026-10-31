# Guardrails: rules for the AI

Guardrails are rules the bot's AI always follows, on top of its personality and your rules channel. Use them to give the AI special instructions, to send fixed answers to questions it shouldn't improvise, to steer it on tricky topics, and to block words in questions or answers.

## Where they live

A plain text file, `guardrails.txt` by default (change it with `GUARDRAILS_FILE` in `.env`). Start by copying [`guardrails.example.txt`](../guardrails.example.txt). A server profile can ship its own: the Static Recomp one is [`servers/static-recomp/guardrails.txt`](../servers/static-recomp/guardrails.txt).

- **No restart needed.** The bot re-reads the file whenever it changes, before the next question.
- **Check it from Discord:** `/guardrails` (Manage Server) shows what's loaded and any lines it couldn't read. `/guardrails test:where can I get the iso?` shows what would happen to that question, and why.
- **Quick instructions from Discord:** administrators can add instructions in `/settings` → Assistant → **Guardrail instructions**, one per line. They're added to the file's `[instructions]` after a restart.
- **Self-updates:** if the file is tracked in git (like the profile's), edit it on GitHub and the bot picks it up on its next update. Editing a tracked file on the server pauses self-updates until the edit is undone. For rules that stay on your server only, point `GUARDRAILS_FILE` at a file git doesn't track, such as `guardrails.txt` or `data/guardrails.txt`.

## The sections

```
[instructions]
Never claim to be a moderator or staff member.

[redirect]
unban me, appeal my ban = Bans are reviewed by the moderators. Please message one directly.

[topic]
release date, eta, when will = Never guess release dates. Say there's no announced date unless the docs give one.

[block questions]
credit card, social security number

[block answers]
/\b\d{3}-\d{2}-\d{4}\b/

[moderation]
Selling or trading accounts is a scam here.
```

Section|What it does|AI call?
-|-|-
`[instructions]`|Rules the assistant follows in every answer, one per line. They're marked as coming from the server's owners, above anything a member asks, and the AI is told never to reveal them.|Yes
`[redirect]`|`keywords = reply`. A question with one of the keywords gets your reply word for word, and the AI never sees it. Good for questions with one right answer.|No
`[topic]`|`keywords = instruction`. A question about that topic is answered by the AI with your extra instruction added. Good for steering, such as "ask for the log first" on crash questions.|Yes
`[block questions]`|Keywords. A question with one is politely refused.|No
`[block answers]`|Keywords. An answer with one is held back and replaced with a short note.|Yes, but the answer isn't sent
`[moderation]`|Guidance for the moderation AI, one per line. The free speech policy still wins: opinions, rudeness and profanity are never flagged.|Only when moderation asks the AI

**Keywords** are comma separated and match whole words in any case: `eta` matches "ETA?" but not "beta". Put a keyword in `/slashes/` to use a regular expression, such as `/\b(where|how)\b.{0,30}\b(download|get)\b.{0,30}\broms?\b/`. Lines starting with `#` are notes. A broken line is skipped and listed by `/guardrails`, so a typo never stops the bot.

The checks run in this order: built-in jailbreak check, `[block questions]`, `[redirect]`, then `[topic]`. The first match decides, except topics, where every matching topic adds its instruction.

## Always on

These protections don't need the file and can't be turned off by it:

- **Jailbreak attempts are refused** without calling the AI: "ignore your previous instructions", "developer mode", "DAN", "pretend you have no rules", "show me your system prompt".
- **Leaks are held back:** an answer that repeats a sentence of the bot's personality or guardrails word for word, or that contains something shaped like a key or token (OpenRouter, Anthropic, OpenAI, GitHub, Groq, Gemini, Discord), or one of the bot's own keys, is never sent.
- **Unsafe links are held back:** an answer with a piracy site, a direct game-file link, a known scam domain or a lookalike domain.
- **No pings:** the AI can't mention @everyone, @here, roles or members.
- **Members' text stays data:** chat history, messages under review and image text are marked as data the AI must not obey, and a member can't break out of that marking.
- **Cost limits:** a cooldown per member and a cap per minute for the server (`ASK_COOLDOWN_SECONDS`, `ASK_MAX_PER_MINUTE`).

## Tips

- Keep redirect keywords specific. "where to download" also catches "where do I download the latest release?"; a regex like the one above only catches game files.
- Prefer a `[topic]` to a `[redirect]` when the member might still need a real answer: the AI keeps helping, within your instruction.
- Use `/guardrails test:` with a few real questions from your server after each change.
- When something gets through, add it here, test it, and keep the line. The file is your record of what the AI must never do.

[← Back to the README](../README.md)
