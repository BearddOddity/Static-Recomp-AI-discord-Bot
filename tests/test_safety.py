"""The bot can't be turned against the server: the safety brake, least-privilege checks,
spending caps, answer screening and secrets kept private."""

import asyncio
import os
import stat
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from bot import config
from bot.ai.cli import CLIProvider
from bot.assist import BLOCKED_REPLY, screen_reply
from bot.main import PAUSED_REPLY, AssistantBot, safety_problems
from bot.moderation import Classifier, LinkChecker, Moderator, Post
from bot.moderation.brake import SafetyBrake
from bot.moderation.cases import CaseStore, protect
from bot.moderation.engine import Verdict
from bot.moderation.review import BRAKE_NOTE

from test_cases import FakeDiscord
from test_moderation import FakeAI


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("MOD_LOG_CHANNEL_ID", "99")
    return config.load_settings(require_discord=False)


@pytest.fixture
def store():
    s = CaseStore(":memory:")
    yield s
    s.close()


AI_SCAM = Verdict("scam", 0.9, ("looks like a scam",), "ai", score=30)
CERTAIN_SCAM = Verdict("scam", 1.0, ("known scam domain",), "rules", score=100)


# --- the safety brake -------------------------------------------------------------------

def test_brake_comes_on_when_the_ai_flags_too_many_members(tmp_path):
    brake = SafetyBrake(tmp_path / "brake.json", limit=3)
    assert [brake.record(member, now=n) for n, member in enumerate([1, 2, 2, 3])] == [False] * 4  # 3 members: fine
    assert brake.record(4, now=10)  # the 4th different member in 10 minutes
    assert brake.auto_engaged and not brake.manual
    assert brake.holds(certain=False) and not brake.holds(certain=True)  # known scam links still act
    assert SafetyBrake(tmp_path / "brake.json").auto_engaged  # survives a restart
    brake.release()
    assert not brake.engaged and not SafetyBrake(tmp_path / "brake.json").engaged


def test_old_actions_fall_out_of_the_window():
    brake = SafetyBrake(limit=2)
    brake.record(1, now=0)
    brake.record(2, now=1)
    assert not brake.record(3, now=700)  # the first two were more than 10 minutes ago
    assert not SafetyBrake(limit=0).record(1)  # 0 = never by itself


def test_manual_brake_stops_everything(tmp_path):
    brake = SafetyBrake(tmp_path / "b.json")
    brake.engage("mod#1")
    assert brake.holds(certain=True) and brake.holds(certain=False)
    assert "mod#1" in brake.describe()
    assert SafetyBrake(tmp_path / "b.json").manual


def test_held_cases_only_report(settings, store):
    fake = FakeDiscord(settings, store)
    fake.manager.brake.engage("mod")
    message = fake.message()
    case = run(fake.manager.open_from_message(message, Post("x", 2, 3), CERTAIN_SCAM))
    message.delete.assert_not_awaited()
    fake.member.timeout.assert_not_awaited()
    fake.member.send.assert_not_awaited()  # no warning DM
    assert fake.manager.warning_count(1, 2) == 0
    assert BRAKE_NOTE in case.actions
    fake.log_channel.send.assert_awaited_once()  # moderators still get the report

    # The next violation joins the case, still without automatic action.
    run(fake.manager.open_from_message(fake.message(), Post("x", 2, 3), CERTAIN_SCAM))
    assert fake.manager.warning_count(1, 2) == 0


def test_brake_trips_by_itself_and_tells_the_moderators(settings, store):
    fake = FakeDiscord(settings, store)
    fake.manager.brake = SafetyBrake(limit=1)
    fake.manager.client.notify_mods = AsyncMock()
    first = fake.message()
    run(fake.manager.open_from_message(first, Post("x", 2, 3), AI_SCAM))
    first.delete.assert_awaited_once()

    fake.member.id = 5  # a second member flagged by the AI within minutes
    second = fake.message()
    case = run(fake.manager.open_from_message(second, Post("x", 5, 3), AI_SCAM))
    second.delete.assert_not_awaited()
    assert BRAKE_NOTE in case.actions
    assert "Safety brake on" in fake.manager.client.notify_mods.await_args.args[0]

    fake.member.id = 6  # a certain hit still acts while the automatic brake is on
    third = fake.message()
    run(fake.manager.open_from_message(third, Post("x", 6, 3), CERTAIN_SCAM))
    third.delete.assert_awaited_once()


def test_moderators_can_still_warn_by_hand_while_braked(settings, store):
    fake = FakeDiscord(settings, store)
    fake.manager.brake.engage("mod")
    run(fake.manager.open_manual(fake.guild, fake.member, fake.make_member(77), "spam"))
    assert fake.manager.warning_count(1, 2) == 1


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "k")
    monkeypatch.setenv("MOD_DB_PATH", str(tmp_path / "mod.sqlite3"))
    monkeypatch.delenv("WELCOME_CHANNEL_ID", raising=False)
    bot = AssistantBot(config.load_settings())
    yield bot
    bot.store.close()


def test_the_brake_pauses_ai_answers(bot):
    bot.ai = FakeAI("hello")
    bot.brake.engage("mod")
    parts, _ = run(bot.answer("hi", None))
    assert parts == [PAUSED_REPLY] and bot.ai.calls == []
    bot.brake.release()
    assert run(bot.answer("hi", None))[0] == ["hello"]


def test_brake_command_is_for_moderators():
    from bot import main

    assert main.brake_command.default_permissions.moderate_members and main.brake_command.checks


# --- least privilege ---------------------------------------------------------------------

class Role:
    def __init__(self, name, position, default=False, managed=False, **perms):
        self.name, self.position, self._default, self.managed = name, position, default, managed
        self.permissions = discord.Permissions(**perms)

    def is_default(self):
        return self._default

    def __lt__(self, other):
        return self.position < other.position


def guild_with(bot_perms, roles, bot_position=5):
    me = SimpleNamespace(guild_permissions=discord.Permissions(**bot_perms), top_role=Role("Bot", bot_position))
    return SimpleNamespace(me=me, roles=roles)


def test_safe_setup_has_no_problems():
    from bot.main import REQUIRED_PERMISSIONS

    perms = {name: True for name, on in REQUIRED_PERMISSIONS if on}
    roles = [Role("@everyone", 0, default=True), Role("Member", 1), Role("Mods", 9, ban_members=True)]
    assert safety_problems(guild_with(perms, roles)) == []


def test_administrator_and_extra_permissions_are_flagged():
    assert "Administrator" in safety_problems(guild_with({"administrator": True}, []))[0]
    problem = safety_problems(guild_with({"manage_roles": True, "manage_webhooks": True}, []))[0]
    assert "Manage Roles" in problem and "Manage Webhooks" in problem


def test_bot_above_staff_is_flagged():
    roles = [Role("Mods", 3, ban_members=True), Role("Admins", 4, administrator=True), Role("Member", 1)]
    problem = safety_problems(guild_with({}, roles))[0]
    assert "Mods" in problem and "Admins" in problem and "Member" not in problem


# --- spending and answers ------------------------------------------------------------------

def test_moderation_ai_has_a_per_minute_budget():
    ai = FakeAI()
    mod = Moderator(LinkChecker(), Classifier(ai), ai_scan="all", ai_per_minute=2)
    for n in range(4):
        run(mod.review(Post(f"message number {n}", n, 1)))
    assert len(ai.calls) == 2  # the rest are judged by the rules alone


def test_disguised_links_show_where_they_go():
    reply = screen_reply("See [the GitHub repo](https://github.com/example/switch-tools) for more.", LinkChecker())
    assert reply == "See the GitHub repo (<https://github.com/example/switch-tools>) for more."


@pytest.mark.parametrize("reply", ["Join discord.gg/abc123 for help", "https://discord.com/invite/xyz"])
def test_answers_never_advertise_other_servers(reply):
    assert screen_reply(reply, LinkChecker()) == BLOCKED_REPLY


# --- secrets ---------------------------------------------------------------------------

def test_command_line_ai_never_sees_the_bots_tokens(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "discord-secret")
    monkeypatch.setenv("HEALTH_GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("SOME_TOOL_KEY", "tool-key")
    script = "import os,sys; sys.stdin.read(); print(os.getenv('DISCORD_TOKEN'), os.getenv('HEALTH_GITHUB_TOKEN'), os.getenv('SOME_TOOL_KEY'))"
    provider = CLIProvider(f'"{sys.executable}" -c "{script}"')
    assert run(provider.complete([])) == "None None tool-key"


@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX only")
def test_data_files_are_private(tmp_path):
    store = CaseStore(str(tmp_path / "data" / "mod.sqlite3"))
    store.close()
    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "data" / "mod.sqlite3").stat().st_mode) == 0o600
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=x")
    env.chmod(0o644)
    protect(env, 0o600)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    protect(tmp_path / "missing", 0o600)  # nothing to do, no error



def test_brake_settings_need_an_administrator():
    from bot import runtime_settings as rs

    assert rs.FIELDS["MOD_BRAKE_LIMIT"].admin and rs.FIELDS["MOD_AI_MAX_PER_MINUTE"].admin
