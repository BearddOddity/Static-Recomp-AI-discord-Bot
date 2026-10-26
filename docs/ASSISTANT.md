# The assistant

How the bot answers questions, and how it searches your GitHub docs.


The bot is a general AI assistant, not just a docs search. Its personality is inspired by Tony Stark's J.A.R.V.I.S.: calm, quick, precise, polite, with a dry wit, and it answers the question first, then offers what you'll probably need next. It helps with anything: your community's projects, programming and debugging, games, hardware, general knowledge, writing or just conversation. Your GitHub docs are its reference: it uses and cites them when they're relevant, and answers from its own knowledge when they aren't.

- **Ask it:** `/ask`, mention it (`@Bot why does my build fail?`), or reply to one of its answers to follow up. It remembers the last few turns, so "and on Windows?" works.
- **Assistant channels:** list channel IDs in `ASSISTANT_CHANNEL_IDS` and it answers every message there, like a chat window.
- **It knows where it is:** who's asking, the server and channel, today's date, and the last few messages in the channel (`CHAT_CONTEXT_MESSAGES`), so "what do you think of Sam's idea?" makes sense to it.
- **Screenshots:** attach an image (an error dialog, a crash log, a settings screen) and ask about it. This needs a model that accepts images.
- **Its own rules still apply:** it follows the server's rules channel and the anti-piracy policy, and it says when it isn't sure instead of making things up. It can't browse the web.
- **Personality:** set `AI_SYSTEM_PROMPT` to replace it. `{name}`, `{server}` and `{community}` are filled in.

Long answers arrive in up to three messages instead of being cut off. `ASK_ON_MENTION=false` turns off mentions and replies (`/ask` and assistant channels still work).

## Answering from GitHub docs

List the repos to learn from in `DOCS_REPOS`, comma separated, as `owner/repo[@branch][:folder]`. Add `.wiki` to a repo name to read its GitHub wiki instead:

```
DOCS_REPOS=owner/switch-tools,owner/switch-tools.wiki,owner/ps3-tools@main:docs
```

Wikis are read with `git clone`, so `git` must be installed on the machine running the bot. A wiki's pages count as part of its repo for keyword routing.

### Keyword routing

When a question names a platform, `DOCS_ROUTES` makes the bot answer only from that project's docs, so a PS3 question never gets Switch instructions:

```
DOCS_ROUTES=switch 2,switch2=owner/switch2-tools; switch=owner/switch-tools; ps3=owner/ps3-tools
```

Each rule is `keywords=owner/repo`, and rules are separated by `;`. Keywords match whole words and ignore case. Rules are checked in order, and a matched keyword is removed before later rules run. That is why "Switch 2" goes to the first repo and is not also counted as plain "Switch". A question naming two platforms searches both. A question with no keyword searches every repo. Each route's repo must also be listed in `DOCS_REPOS`.

At startup, and every `DOCS_REFRESH_MINUTES` after, the bot downloads Markdown, reStructuredText and text files from those repos and splits them into sections by heading. For each question, it finds the most relevant sections, gives them to the AI as reference, and links the source files under the answer. Questions that have nothing to do with the docs (small talk, other subjects) get no excerpts, so the AI answers freely. Server managers can run `/reload-docs` after updating the docs.

**How the search finds the right section:**

- Each section knows the headings above it ("Installing > Linux"), so a short subsection is still found by its topic.
- Sections are split at paragraph breaks, and code blocks are never cut in half.
- Word forms match ("configured", "configuration"), and so do common synonyms ("setup" and "install", "crash" and "freeze") and small typos ("recompilr").
- Words that appear side by side in the docs ("save files") rank higher, and no single file can fill every slot.
- If the question's words barely match the docs, the AI first rephrases it into the words the docs use, then searches again (`DOCS_AI_QUERY=fallback`, the default). `always` does this for every question, for one extra AI call each; `off` never does.

- Without `GITHUB_TOKEN`, GitHub allows 60 API requests an hour, which a shared server can use up. When that happens, the bot reads public repos with `git clone` instead. A read-only fine-grained token raises the limit and allows private repos.
- Only documentation files are read, not source code. Files over 200 KB, build files such as `CMakeLists.txt`, and folders like `third_party/`, `tests/` and `LICENSES/` are skipped.

[← Back to the README](../README.md)
