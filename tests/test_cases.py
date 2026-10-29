import asyncio
import dataclasses
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot import config
from bot.main import build_messages
from bot.moderation.cases import CaseStore
from bot.moderation.classifier import Classifier, parse_verdict
from bot.moderation.engine import Post, Verdict
from bot.moderation.policy import REVIEW_TIMEOUT, is_severe, plan
from bot.moderation.review import CaseManager, available_actions, build_embed, parse_custom_id
from bot.moderation.rules import RulesBook, message_text


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


# --- store -------------------------------------------------------------------


def test_case_round_trip_and_warning_expiry(store):
    case = store.create_case(1, 2, "scam", ["fake giveaway"], channel_id=3, message_id=4, urls=["kegwin.com"])
    case.actions.append("message deleted")
    case.status = "review"
    case.timed_out = True
    store.save(case)
    loaded = store.get(case.id)
    assert loaded.reasons == ["fake giveaway"]
    assert case.urls == ["kegwin.com"] and loaded.urls == []  # links are not stored
    assert loaded.actions == ["message deleted"] and loaded.timed_out and loaded.status == "review"
    assert [c.id for c in store.cases_in_review()] == [case.id]

    store.add_warning(1, 2, "fake giveaway", case.id)
    store.add_warning(1, 2, "old one", None)
    store._db.execute("UPDATE warnings SET created_at = ? WHERE reason = 'old one'", (time.time() - 100 * 86400,))
    assert [w.reason for w in store.active_warnings(1, 2, expiry_days=90)] == ["fake giveaway"]
    assert len(store.active_warnings(1, 2, expiry_days=0)) == 2  # 0 = never expire

    store.revoke_warnings_for_case(case.id)
    assert store.active_warnings(1, 2, expiry_days=90) == []


def test_rules_are_saved(store):
    store.save_rules(1, 10, "1. Be nice")
    store.save_rules(1, 10, "1. Be nice\n2. No piracy")
    assert store.load_rules() == {1: "1. Be nice\n2. No piracy"}


# --- warning ladder ------------------------------------------------------------


def test_warnings_escalate_to_review_at_the_limit(settings):
    assert settings.mod_warn_limit == 3
    assert not plan("piracy", False, 0, settings).review_timeout
    assert not plan("piracy", False, 1, settings).review_timeout
    third = plan("piracy", False, 2, settings)
    assert third.review_timeout and third.warning_number == 3


def test_severe_scams_follow_the_ladder_unless_review_is_on(settings):
    severe = Verdict("scam", 0.95, ("fake giveaway",), "ai")
    assert is_severe(severe, settings)
    # By default every scam is a warning; the third one times the member out.
    assert not plan("scam", True, 0, settings).review_timeout
    on = dataclasses.replace(settings, mod_severe_scam_review=True)
    assert plan("scam", True, 0, on).review_timeout
    assert not is_severe(Verdict("scam", 0.8, (), "ai"), settings)
    assert is_severe(Verdict("scam", 0.6, (), "rules", score=100), settings)
    assert not is_severe(Verdict("piracy", 0.99, (), "ai"), settings)


def test_warnings_delete_the_message_by_default(settings):
    for label in ("scam", "piracy", "hate_speech", "rule_violation"):
        assert plan(label, False, 0, settings).delete_message, label
    assert not plan("manual", False, 0, settings).delete_message  # no message to delete
    flagging = dataclasses.replace(settings, mod_action="flag")
    assert not plan("scam", False, 0, flagging).delete_message
    assert plan("piracy", False, 0, flagging).delete_message


def test_review_timeout_fits_discords_28_day_limit():
    assert REVIEW_TIMEOUT < timedelta(days=28)


# --- buttons -------------------------------------------------------------------


def test_buttons_depend_on_case_state(store):
    case = store.create_case(1, 2, "scam", ["x"], channel_id=3, message_id=4)
    assert available_actions(case) == ["ban", "timeout", "dismiss", "delete"]
    case.timed_out, case.message_deleted = True, True
    assert available_actions(case) == ["ban", "lift", "dismiss"]
    case.status = "closed"
    assert available_actions(case) == []
    assert parse_custom_id("case:12:ban") == (12, "ban")
    assert parse_custom_id("other:12:ban") is None and parse_custom_id("case:x:ban") is None


# --- case manager with fake Discord objects --------------------------------------


class FakeDiscord:
    def __init__(self, settings, store):
        self.log_channel = MagicMock(spec=discord.TextChannel)
        self.log_channel.id = 99
        self.log_channel.send = AsyncMock(return_value=MagicMock(id=500))
        self.log_channel.get_partial_message.return_value.edit = AsyncMock()
        self.log_channel.get_partial_message.return_value.fetch = AsyncMock(return_value=MagicMock(embeds=[]))
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = 1
        self.guild.name = "Recomp"
        self.guild.ban = AsyncMock()
        self.member = self.make_member(2)
        self.guild.get_member.return_value = self.member
        client = MagicMock()
        client.get_channel.return_value = self.log_channel
        client.get_guild.return_value = self.guild
        self.manager = CaseManager(client, store, settings)

    @staticmethod
    def make_member(user_id, **permissions):
        member = MagicMock(spec=discord.Member)
        member.id = user_id
        member.bot = False
        member.timeout = AsyncMock()
        member.send = AsyncMock()
        member.guild_permissions = discord.Permissions(**permissions)
        return member

    def message(self, text="free nitro"):
        message = MagicMock()
        message.id = 4
        message.content = text
        message.delete = AsyncMock()
        message.author = self.member
        message.guild = self.guild
        message.channel.id = 3
        return message

    def click(self, case_id, action, **permissions):
        interaction = MagicMock()
        interaction.data = {"custom_id": f"case:{case_id}:{action}"}
        interaction.user = self.make_member(77, **permissions)
        interaction.guild = self.guild
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        run(self.manager.handle_button(interaction))
        return interaction


SUSPECTED_PIRACY = Verdict("piracy", 0.8, ("iso link",), "ai")


def dm_text(embed: discord.Embed) -> str:
    return " ".join([embed.title or ""] + [f"{f.name} {f.value}" for f in embed.fields])


def flag(fake, verdict=SUSPECTED_PIRACY):
    return run(fake.manager.open_from_message(fake.message(), Post("free nitro", 2, 3), verdict))


def test_first_violation_warns_dms_and_reports(settings, store):
    fake = FakeDiscord(settings, store)
    case = flag(fake)
    assert case.status == "warned" and not case.timed_out and case.message_deleted
    fake.member.timeout.assert_not_awaited()
    dm = fake.member.send.await_args.kwargs["embed"]
    assert "1 of 3" in dm_text(dm) and "warning" in dm.title
    sent = fake.log_channel.send.await_args.kwargs
    # The message is already gone, so there's no Delete button.
    assert [b.custom_id for b in sent["view"].children] == [
        f"case:{case.id}:ban", f"case:{case.id}:timeout", f"case:{case.id}:dismiss",
    ]
    assert store.get(case.id).log_message_id == 500


def test_third_warning_times_out_until_review(settings, store):
    fake = FakeDiscord(settings, store)
    flag(fake)
    flag(fake)
    case = flag(fake)
    assert case.status == "review" and case.timed_out
    assert fake.member.timeout.await_args.args[0] == REVIEW_TIMEOUT
    assert "timed out" in dm_text(fake.member.send.await_args.kwargs["embed"])
    fake.guild.ban.assert_not_awaited()  # the bot never bans by itself


def test_severe_scam_times_out_on_first_offence_when_enabled(settings, store):
    fake = FakeDiscord(dataclasses.replace(settings, mod_severe_scam_review=True), store)
    case = flag(fake, Verdict("scam", 0.97, ("fake MrBeast casino",), "ai"))
    assert case.status == "review" and case.timed_out


def test_ban_needs_permission_and_confirmation(settings, store):
    fake = FakeDiscord(dataclasses.replace(settings, mod_severe_scam_review=True), store)
    case = flag(fake, Verdict("scam", 0.97, ("x",), "ai"))

    denied = fake.click(case.id, "ban", moderate_members=True)
    assert "ban members" in denied.response.send_message.await_args.args[0]

    asked = fake.click(case.id, "ban", ban_members=True)
    confirm = asked.response.send_message.await_args.kwargs["view"].children[0]
    assert confirm.custom_id == f"case:{case.id}:ban_confirm"
    fake.guild.ban.assert_not_awaited()

    fake.click(case.id, "ban_confirm", ban_members=True)
    fake.guild.ban.assert_awaited_once()
    closed = store.get(case.id)
    assert closed.status == "closed" and closed.decision.startswith("banned by")


def test_dismiss_removes_warning_and_lifts_timeout(settings, store):
    fake = FakeDiscord(dataclasses.replace(settings, mod_severe_scam_review=True), store)
    case = flag(fake, Verdict("scam", 0.97, ("x",), "ai"))
    assert fake.manager.warning_count(1, 2) == 1

    fake.click(case.id, "dismiss", moderate_members=True)
    assert fake.manager.warning_count(1, 2) == 0
    assert fake.member.timeout.await_args.args == (None,)
    closed = store.get(case.id)
    assert closed.status == "closed" and not closed.timed_out and "not a violation" in closed.decision
    # The report is redrawn without buttons.
    edit = fake.log_channel.get_partial_message.return_value.edit
    assert edit.await_args.kwargs["view"].children == []


def test_lift_keeps_the_warning(settings, store):
    fake = FakeDiscord(dataclasses.replace(settings, mod_severe_scam_review=True), store)
    case = flag(fake, Verdict("scam", 0.97, ("x",), "ai"))
    fake.click(case.id, "lift", moderate_members=True)
    assert fake.manager.warning_count(1, 2) == 1
    assert store.get(case.id).decision.startswith("timeout lifted")


def test_closed_case_ignores_more_clicks(settings, store):
    fake = FakeDiscord(settings, store)
    case = flag(fake)
    fake.click(case.id, "dismiss", moderate_members=True)
    again = fake.click(case.id, "lift", moderate_members=True)
    assert "already closed" in again.response.send_message.await_args.args[0]


def test_manual_warning_counts_toward_the_limit(settings, store):
    fake = FakeDiscord(settings, store)
    moderator = fake.make_member(77, moderate_members=True)
    run(fake.manager.open_manual(fake.guild, fake.member, moderator, "spamming"))
    run(fake.manager.open_manual(fake.guild, fake.member, moderator, "spamming again"))
    case = flag(fake)
    assert case.status == "review"


def test_embed_shows_status_and_review_note(settings, store):
    case = store.create_case(1, 2, "scam", ["fake giveaway"], confidence=0.9)
    case.status = "review"
    embed = build_embed(case, 1, settings)
    names = [f.name for f in embed.fields]
    assert "🧑\u200d⚖️ Your decision" in names and embed.title == f"🎣 Case #{case.id} · Scam"
    assert "Waiting for a moderator" in embed.description and "> " not in embed.description
    assert "🟧⬜⬜" in embed.fields[names.index("⚠️ Warnings")].value


def test_embed_quotes_the_message_without_pinging(settings, store):
    case = store.create_case(1, 2, "scam", ["fake giveaway"], content="hi @everyone\nfree nitro", channel_id=3)
    case.message_deleted = True
    embed = build_embed(case, 1, settings)
    assert "> hi @\u200beveryone\n> free nitro" in embed.description
    assert "Warning given" in embed.description
    assert "message removed" in embed.fields[1].value
    case.status, case.decision, case.decided_by, case.decided_at = "closed", "banned by mod", 9, time.time()
    closed = build_embed(case, 1, settings)
    assert "Closed:" in closed.description and any(f.name == "🕒 Decided" for f in closed.fields)


# --- #rules ----------------------------------------------------------------------


def test_rules_text_includes_embeds():
    message = MagicMock(content="Server rules:")
    embed = discord.Embed(title="Rule 1", description="No piracy")
    embed.add_field(name="Rule 2", value="No scams")
    message.embeds = [embed]
    assert message_text(message) == "Server rules:\nRule 1\nNo piracy\nRule 2\nNo scams"


def test_rules_book_reads_channel_and_saves(store):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10
    channel.name = "rules"

    async def history(**kwargs):
        for text in ("1. No piracy", "2. Be respectful"):
            yield MagicMock(content=text, embeds=[])

    channel.history = history
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    guild.name = "Recomp"
    guild.get_channel.return_value = channel

    book = RulesBook(store, channel_id=10)
    assert run(book.refresh(guild)) == "1. No piracy\n\n2. Be respectful"
    assert book.is_rules_channel(guild, 10) and not book.is_rules_channel(guild, 11)
    assert RulesBook(store).get(1) == "1. No piracy\n\n2. Be respectful"  # survives restarts


def test_classifier_and_ask_receive_the_rules():
    class Capture:
        name, supports_images = "fake", True
        sent = None

        async def complete(self, messages):
            Capture.sent = messages
            return '{"label": "rule_violation", "confidence": 0.9, "reasons": ["Rule 3: no self-promotion"]}'

    verdict = run(Classifier(Capture()).classify("join my server!", [], [], [], rules="3. No self-promotion"))
    assert verdict.label == "rule_violation"
    assert "3. No self-promotion" in Capture.sent[0].content

    messages = build_messages("Be helpful.", "where can I get the iso?", [], rules="1. No piracy")
    assert any("1. No piracy" in m.content for m in messages if m.role == "system")
    assert parse_verdict('{"label": "rule_violation", "confidence": 1}').label == "rule_violation"


# --- free speech and hate speech -------------------------------------------------


def test_hate_speech_always_goes_to_review(settings):
    hate = Verdict("hate_speech", 0.8, ("slur aimed at a member",), "ai")
    assert is_severe(hate, settings)
    first = plan("hate_speech", True, 0, settings)
    assert first.review_timeout and first.warning_number == 1
    off = dataclasses.replace(settings, mod_severe_scam_review=False)
    assert plan("hate_speech", True, 0, off).review_timeout


def test_classifier_prompt_protects_free_speech():
    from bot.moderation.classifier import RULES_SECTION, SYSTEM_PROMPT

    assert "FREE SPEECH" in SYSTEM_PROMPT and "NEVER flag a message for its opinions" in SYSTEM_PROMPT
    assert "never a reason to flag" in RULES_SECTION
    assert parse_verdict('{"label": "hate_speech", "confidence": 0.9}').label == "hate_speech"


def test_hate_speech_from_ai_times_out_first_time(settings, store):
    fake = FakeDiscord(settings, store)
    case = flag(fake, Verdict("hate_speech", 0.85, ("slur aimed at a member",), "ai"))
    assert case.status == "review" and case.timed_out
    assert build_embed(case, 1, settings).title == f"🛑 Case #{case.id} · Hate speech"


def test_burst_of_violations_makes_one_report(settings, store):
    fake = FakeDiscord(settings, store)
    first = flag(fake)
    second = flag(fake, Verdict("scam", 0.8, ("fake giveaway",), "ai"))
    assert second.id == first.id
    assert fake.log_channel.send.await_count == 1  # one report, then edited
    merged = store.get(first.id)
    assert merged.label == "scam"  # the more serious label wins
    assert "fake giveaway" in merged.reasons and fake.manager.warning_count(1, 2) == 2
    assert fake.log_channel.get_partial_message.return_value.edit.await_count >= 1


# --- Discord AutoMod -------------------------------------------------------------


def automod_event(fake, *, rule_id=7, slurs=True, message_id=None, trigger=discord.AutoModRuleTriggerType.keyword_preset):
    execution = MagicMock(spec=discord.AutoModAction)
    execution.guild_id, execution.guild = 1, fake.guild
    execution.user_id, execution.member = 2, fake.member
    execution.rule_id, execution.rule_trigger_type = rule_id, trigger
    execution.channel_id, execution.message_id = 3, message_id
    execution.content, execution.matched_keyword, execution.matched_content = "bad words", "badword", None
    rule = MagicMock()
    rule.trigger.presets = discord.AutoModPresets(slurs=slurs)
    execution.fetch_rule = AsyncMock(return_value=rule)
    return execution


def test_automod_slur_filter_opens_hate_speech_case_once(settings, store):
    fake = FakeDiscord(settings, store)
    case = run(fake.manager.open_from_automod(automod_event(fake)))
    assert case.label == "hate_speech" and case.status == "review" and case.timed_out
    assert case.message_deleted and "AutoMod blocked the message" in case.actions
    assert "||badword||" in case.reasons[1]
    # AutoMod sends one event per action (block + alert); the second is ignored.
    assert run(fake.manager.open_from_automod(automod_event(fake))) is None
    assert fake.log_channel.send.await_count == 1


def test_automod_profanity_filter_is_free_speech(settings, store):
    fake = FakeDiscord(settings, store)
    assert run(fake.manager.open_from_automod(automod_event(fake, slurs=False))) is None
    custom = automod_event(fake, rule_id=55, trigger=discord.AutoModRuleTriggerType.keyword)
    assert run(fake.manager.open_from_automod(custom)) is None
    fake.manager.settings = dataclasses.replace(settings, mod_hate_automod_rule_ids=frozenset({55}))
    assert run(fake.manager.open_from_automod(custom)).label == "hate_speech"


def test_certain_piracy_is_deleted_but_follows_the_ladder(settings, store):
    certain = Verdict("piracy", 1.0, ("shared a game file: halo.iso (disc image)",), "rules", score=100)
    assert is_severe(certain, settings)
    first = plan("piracy", True, 0, settings)
    flagging = dataclasses.replace(settings, mod_piracy_action="flag")
    assert plan("piracy", True, 0, flagging).delete_message  # always, even when set to "flag"
    assert first.delete_message and not first.review_timeout
    assert plan("piracy", True, 2, settings).review_timeout   # third warning
    guess = Verdict("piracy", 0.8, ("asks for an iso",), "ai", score=20)
    assert not is_severe(guess, settings)
    assert not plan("piracy", False, 0, flagging).delete_message

    fake = FakeDiscord(settings, store)
    message = fake.message("here")
    case = run(fake.manager.open_from_message(message, Post("here", 2, 3), certain))
    message.delete.assert_awaited_once()
    assert case.status == "warned" and case.message_deleted


def test_ask_always_carries_the_anti_piracy_policy():
    messages = build_messages("Be helpful.", "where do I get halo 3 iso", [])
    assert any("anti-piracy" in m.content for m in messages if m.role == "system")
