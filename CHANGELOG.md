# Changelog

## v0.1.0-alpha (2026-10-26)

First public test build. Expect rough edges; please report problems as GitHub issues.

### Assistance
- `/ask` answers questions using documentation pulled from GitHub: xboxrecomp, ps3recomp, ReXGlue and the ReXGlue wiki, with source links under every answer.
- Keyword routing: "360" / "Xbox 360" → ReXGlue, "Xbox" → xboxrecomp, "PS3" → ps3recomp.
- Any AI backend: Claude, OpenRouter (free models, nothing to host), Groq, Gemini, any OpenAI-compatible API, a local Ollama model, or a command-line tool.
- Follows the server's rules channel and the anti-piracy policy in its answers.

### Moderation
- Scam detection: known scam and lookalike domains, link shorteners, scam phrasing (crypto giveaways, promo codes, free Nitro, "withdrawal success"), new accounts and cross-posting, plus an AI that reads screenshots for image-only scams.
- Anti-piracy: game-file attachments (.elf, .xbe, .xex, .iso and other disc images, ROMs, packages, keys), including inside .zip files, piracy-site links and direct game-file links are always removed.
- Free speech: opinions, rudeness and profanity are never moderated. Hate speech (via Discord AutoMod's Slurs filter or the AI) times the member out pending review.
- Links are welcome: YouTube, GitHub, GIFs and wikis are always safe; other links only count when they're scams or game files.
- Warning ladder: warnings by DM, a timeout until a moderator reviews at 3 warnings, and bans only by a moderator.
- Case reports in a mod-log channel with Ban, Timeout, Lift timeout, Not a violation and Delete buttons; cases, warnings and the rules copy are stored in SQLite.
- Reads the rules channel and re-reads it whenever it changes.
- Edited messages are checked again; a burst of violations becomes one case.

### Commands
`/ask`, `/warn`, `/warnings`, `/status` (setup health check), `/reload-docs`.

### Setup
- [SETUP.md](SETUP.md) step-by-step guide, with an invite link that has exactly the permissions needed.
- `start.bat` (Windows), `start.sh` (Mac/Linux), Dockerfile and docker-compose.
- `python -m bot.moderation.check` to test images, files or text without Discord.

### Known limitations
- Not yet tested in a live Discord server at scale; this alpha is for that.
- In the default `MOD_AI_SCAN=suspicious` mode, the AI only sees posts that already look risky. Hate speech in plain text relies on Discord AutoMod's Slurs filter, and rules-channel violations that don't look like scams are mostly missed. `MOD_AI_SCAN=all` covers everything, at one AI call per message.
- Image-only scams need a vision-capable model (`MOD_AI_MODEL`).
- Docs search is keyword based.
- The Docker image hasn't been built and tested yet.
