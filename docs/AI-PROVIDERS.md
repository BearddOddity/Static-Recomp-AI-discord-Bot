# AI providers

Pick any AI backend: free hosted models, Claude, a model on your own machine, or a command-line tool.

## Quickstart with OpenRouter (free, nothing to host)

1. Make an account at https://openrouter.ai and create a key at https://openrouter.ai/keys.
2. In `.env`:

   ```
   AI_PROVIDER=openrouter
   OPENAI_COMPAT_API_KEY=sk-or-...
   ```

That's enough: with no model set, the bot reads OpenRouter's list of free models and uses one that works, preferring one that can read images for moderation. To choose yourself, set `OPENAI_COMPAT_MODEL` (and `MOD_AI_MODEL`) to models from https://openrouter.ai/models?max_price=0, where free IDs end in `:free`. Comma-separate backups: `OPENAI_COMPAT_MODEL=first:free,second:free`. When every listed model is busy or gone, the bot falls back to other free models (`OPENROUTER_FREE_FALLBACK=true`) and tells you, as described in [Hosting → AI health](HOSTING.md#ai-health).

Free OpenRouter models have per-minute and daily request limits. Adding a small credit balance raises them. The bot sends `X-Title` (and `HTTP-Referer` when `OPENROUTER_SITE_URL` is set) so OpenRouter can credit your app.

## Choosing an AI backend

Set `AI_PROVIDER` in `.env`. You never need to run a model server for the hosted options.

| `AI_PROVIDER` | What it is | Cost | Set up |
| --- | --- | --- | --- |
| `anthropic` | Claude via the Anthropic API | Paid | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `openrouter` | Many models, including free `:free` ones | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `groq` | Fast hosted open models | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `gemini` | Google Gemini | Free tier | `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` |
| `openai_compatible` | Any other OpenAI-compatible endpoint | Varies | Also `OPENAI_COMPAT_BASE_URL` |
| `ollama` | A model on the bot's own machine | Free, needs hardware | `OLLAMA_HOST`, `OLLAMA_MODEL` |
| `cli` | A command-line AI tool (Kilo, OpenCode, etc.) | Depends on the tool | `AI_CLI_COMMAND` |

Free tiers have rate and daily limits, and the list of free models changes over time. Check each provider's current pricing page before relying on one.

For `cli`, the bot runs `AI_CLI_COMMAND` without a shell. It writes the conversation to stdin and reads the reply from stdout. Quote any arguments that contain spaces. Test the command in a terminal first.

> **Warning for `cli`:** coding agents such as Kilo or OpenCode can read files and run commands. With `cli`, anyone in your server can send that agent a prompt. Run it in a non-interactive, read-only mode, ideally in a sandbox or container. Never run it in a folder that holds secrets.

[← Back to the README](../README.md)
