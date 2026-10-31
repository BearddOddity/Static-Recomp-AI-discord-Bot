"""/status: a checklist that says what works, and for every problem exactly how to fix it."""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from bot import config
from bot.main import REQUIRED_PERMISSIONS, AssistantBot, status_checks
from bot.status import FIX, OK, Check, fix, off, ok, render

from test_moderation import FakeAI


def run(coro):
    return asyncio.run(coro)


def test_all_good_says_so_in_the_title():
    embed = render([ok("Locked to this server"), off("Welcome: off")], "Bot", "1.0")
    assert embed.title.startswith("✅ All good")
    assert embed.colour.value == 0x2ECC71
    assert "✅ Locked to this server" in embed.description and "➖ Welcome: off" in embed.description
    assert "Needs fixing" not in embed.description


def test_problems_come_first_with_their_fix():
    checks = [ok("Docs loaded"), fix("No case reports channel.", "Pick one in /settings."), fix("AutoMod off.", "Turn it on.")]
    embed = render(checks, "Bot", "1.0")
    assert embed.title == "❌ 2 things to fix" and embed.colour.value == 0xE74C3C
    text = embed.description
    assert text.index("Needs fixing") < text.index("No case reports") < text.index("Working") < text.index("Docs loaded")
    assert "**Fix:** Pick one in /settings." in text
    assert render(checks[:2], "Bot", "1.0").title == "❌ 1 thing to fix"


def test_a_long_report_never_cuts_the_problems():
    checks = [fix("Broken.", "Mend it.")] + [ok("x" * 300) for _ in range(30)]
    embed = render(checks, "Bot", "1.0")
    assert len(embed.description) <= 4096
    assert "**Fix:** Mend it." in embed.description


class FakeGuild:
    def __init__(self, perms: dict, channels: dict, slurs: bool = True, automod_readable: bool = True):
        self.id = 1
        role = SimpleNamespace(position=5, permissions=discord.Permissions(**perms), is_default=lambda: False)
        self.me = SimpleNamespace(guild_permissions=role.permissions, top_role=role, roles=[role])
        self.roles, self.rules_channel, self.text_channels = [], None, []
        self._channels, self._slurs, self._readable = channels, slurs, automod_readable

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_automod_rules(self):
        if not self._readable:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no")
        presets = SimpleNamespace(slurs=self._slurs)
        return [SimpleNamespace(enabled=True, trigger=SimpleNamespace(presets=presets))]


@pytest.fixture
def make_bot(monkeypatch, tmp_path):
    bots = []

    def make(**env):
        monkeypatch.setattr(config, "load_dotenv", lambda: None)
        for name in ("DISCORD_GUILD_ID", "MOD_LOG_CHANNEL_ID", "WELCOME_CHANNEL_ID", "DOCS_REPOS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("DISCORD_TOKEN", "t")
        monkeypatch.setenv("AI_PROVIDER", "openrouter")
        monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "k")
        monkeypatch.setenv("MOD_DB_PATH", str(tmp_path / "mod.sqlite3"))
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        bot = AssistantBot(config.load_settings())
        bot._mod_ai = FakeAI()
        bot._mod_ai.supports_images = True
        bots.append(bot)
        return bot

    yield make
    for bot in bots:
        bot.store.close()


def by_text(checks: list[Check], words: str) -> Check:
    return next(c for c in checks if words in c.text)


def test_a_good_setup_shows_green(make_bot):
    bot = make_bot(DISCORD_GUILD_ID="1", MOD_LOG_CHANNEL_ID="99")
    perms = {name: True for name, on in REQUIRED_PERMISSIONS if on}
    checks = run(status_checks(bot, FakeGuild(perms, {99: SimpleNamespace(mention="#mod-log")})))
    for words in ("Locked to this server", "All the permissions", "No dangerous permissions",
                  "Case reports go to #mod-log", "AutoMod slur filter is on", "Moderation AI"):
        assert by_text(checks, words).state == OK, words


def test_every_problem_says_how_to_fix_it(make_bot):
    bot = make_bot()
    bot.brake.engage("mod")
    guild = FakeGuild({"view_channel": True, "administrator": True}, {}, slurs=False)
    checks = run(status_checks(bot, guild))
    problems = [c for c in checks if c.state == FIX]
    assert all(c.fix for c in problems)
    fixes = {c.text: c.fix for c in problems}
    assert "/brake off" in by_text(problems, "Safety brake").fix
    assert "re-invite" in by_text(problems, "Administrator").fix
    assert "discord.com/oauth2" in by_text(problems, "Missing permissions").fix
    assert "Block words: Slurs" in by_text(problems, "AutoMod").fix
    assert "/settings" in by_text(problems, "case reports").fix
    assert len(fixes) >= 6


def test_thread_permissions_only_matter_with_help_mode(make_bot):
    perms = {name: True for name, on in REQUIRED_PERMISSIONS if on}
    perms.update(create_private_threads=False, manage_threads=False)  # invited with an older link
    checks = run(status_checks(make_bot(), FakeGuild(perms, {})))
    assert by_text(checks, "All the permissions").state == OK
    note = by_text(checks, "Help mode needs")
    assert note.state == "off" and "Create Private Threads" in note.text and "Manage Threads" in note.text
    checks = run(status_checks(make_bot(HELP_ENABLED="true"), FakeGuild(perms, {})))
    assert "Manage Threads" in by_text(checks, "Missing permissions").text


def test_no_guild_id_is_a_tip_not_a_problem(make_bot):
    checks = run(status_checks(make_bot(), FakeGuild({}, {})))
    lock = by_text(checks, "Locked to this server")
    assert lock.state == OK and "DISCORD_GUILD_ID=1" in lock.text


def test_unapplied_settings_are_called_out(make_bot):
    bot = make_bot(DISCORD_GUILD_ID="1")
    bot.settings_pending.add("APPEALS_ENABLED")
    checks = run(status_checks(bot, FakeGuild({}, {})))
    assert checks[0].state == FIX and "Restart now" in checks[0].fix


def test_unreadable_automod_and_blind_moderation_ai(make_bot):
    bot = make_bot()
    bot._mod_ai.supports_images = False
    checks = run(status_checks(bot, FakeGuild({}, {}, automod_readable=False)))
    assert "Manage Server" in by_text(checks, "AutoMod").fix
    assert "MOD_AI_MODEL" in by_text(checks, "can't see images").fix
