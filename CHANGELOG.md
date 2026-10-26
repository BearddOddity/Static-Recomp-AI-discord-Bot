# Changelog

## v0.1.0-alpha (2026-10-26)

First public test build. Expect rough edges; please report problems as GitHub issues.

### Open source template
- MIT licensed: anyone can use, change and build on it.
- The code in `bot/` holds nothing specific to one server. A community's topic, docs repos and keywords live in a server profile (`servers/<name>/server.env`, turned on with `SERVER_PROFILE`), or straight in `.env`. Start your own bot by copying the repo and deleting `servers/static-recomp/`.
- `COMMUNITY_ABOUT` tells the AI what your server is about, for `/ask` and moderation.
- The Static Recomp community's settings ship as `servers/static-recomp/`: xboxrecomp, ps3recomp, ReXGlue and its wiki, with "360" / "Xbox 360" → ReXGlue, "Xbox" → xboxrecomp and "PS3" → ps3recomp.

### Assistance
- A general AI assistant with a personality inspired by Tony Stark's J.A.R.V.I.S.: calm, witty, precise and polite. It answers anything, from the community's projects to programming, games, general knowledge and small talk, and uses the GitHub docs as a reference rather than a limit.
- Knows who's asking, the server, the channel, the date and the last few messages in the channel. Reads screenshots attached to a question (with an image-capable model).
- Optional assistant channels (`ASSISTANT_CHANNEL_IDS`) where it answers every message.
- `/ask` answers questions using documentation pulled from the GitHub repos and wikis you list, with source links under every answer.
- Keyword routing sends a question that names a platform to that project's docs only.
- Ask by mentioning the bot, and ask follow-ups by replying to its answer. Long answers come in up to three messages instead of being cut off.
- Smarter doc search: sections keep their parent headings, code blocks are never split, word forms, synonyms and typos match, side-by-side words rank higher, and one file can't fill every result. When a question's words barely match the docs, the AI rephrases it and searches again.
- If GitHub's API limit is used up, public repos are read with `git clone` instead of being skipped.
- Any AI backend: Claude, OpenRouter (free models, nothing to host), Groq, Gemini, any OpenAI-compatible API, a local Ollama model, or a command-line tool.
- Follows the server's rules channel and the anti-piracy policy in its answers.

### Moderation
- Scam detection: known scam and lookalike domains, link shorteners, scam phrasing (crypto giveaways, promo codes, free Nitro, "withdrawal success"), new accounts and cross-posting, plus an AI that reads screenshots for image-only scams.
- Anti-piracy: game-file attachments (.elf, .xbe, .xex, .iso and other disc images, ROMs, packages, keys), including inside .zip files, piracy-site links and direct game-file links are always removed.
- Free speech: opinions, rudeness and profanity are never moderated. Hate speech (via Discord AutoMod's Slurs filter or the AI) times the member out pending review.
- Links are welcome: YouTube, GitHub, GIFs and wikis are always safe; other links only count when they're scams or game files.
- Warning ladder: each warning deletes the message and DMs the member; the 3rd warning times them out until a moderator reviews; bans only by a moderator. Hate speech skips to the timeout. (`MOD_ACTION=flag` leaves messages up instead.)
- Colour-coded case reports in a mod-log channel: a status line saying whether anyone needs to act, the member's avatar, the quoted message, a warning meter, reasons, AI confidence, links, a timeline, and image evidence. Buttons for Ban (with a confirm step), Timeout, Lift timeout, Not a violation and Delete. Warning DMs use the same look. Cases, warnings and the rules copy are stored in SQLite.
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
- Docs search is keyword based with AI rephrasing; it doesn't understand meaning the way embeddings would.
- Follow-up memory is lost when the bot restarts.
- The assistant can't browse the web, so it doesn't know recent news or prices.
- The Docker image hasn't been built and tested yet.
