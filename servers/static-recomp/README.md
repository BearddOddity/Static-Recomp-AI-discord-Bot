# Static Recomp server profile

Settings for the Discord community about static recompilation of console games: projects that turn a game's machine code into native PC programs. Everything else about the bot is the shared template in `bot/`.

If you started your own bot from this template, delete this whole folder.

## Turning it on

In `.env`, next to your token and channel IDs:

```
SERVER_PROFILE=servers/static-recomp/server.env
```

Anything you also set in `.env` wins over the profile.

## What it sets

- **Topic** (`COMMUNITY_ABOUT`): static recompilation, naming xboxrecomp, ps3recomp and ReXGlue, so the moderation AI knows that debugging talk about `.xex` and `.elf` files is normal here.
- **Docs for `/ask`** (`DOCS_REPOS`): [sp00nznet/xboxrecomp](https://github.com/sp00nznet/xboxrecomp), [sp00nznet/ps3recomp](https://github.com/sp00nznet/ps3recomp), [rexglue/rexglue-sdk](https://github.com/rexglue/rexglue-sdk) and the [ReXGlue wiki](https://github.com/rexglue/rexglue-sdk/wiki).
- **Keyword routing** (`DOCS_ROUTES`): "360" or "Xbox 360" → ReXGlue, "Xbox" → xboxrecomp, "PS3" → ps3recomp. The Xbox 360 rule comes first, so "Xbox 360" never counts as plain "Xbox".

`test_profile.py` checks the routing; `pytest` runs it with the rest of the tests.

## Getting the bot

Download it from <https://github.com/BearddOddity/Static-Recomp-AI-discord-Bot> (Code → Download ZIP) and follow [SETUP.md](../../SETUP.md).
