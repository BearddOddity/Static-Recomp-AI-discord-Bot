import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from bot import community, config
from bot.moderation import appeals
from bot.moderation.cases import CaseStore


def run(coro):
    return asyncio.run(coro)


def make_member(user_id, **perms):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.mention = f"<@{user_id}>"
    member.guild_permissions = SimpleNamespace(
        **{"moderate_members": False, "manage_threads": False, **perms}
    )
    return member


class Recorder:
    """A stand-in for interaction.response / followup that records what was sent."""

    def __init__(self):
        self.sent, self.edits, self.modals, self.deferred = [], [], [], False

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))

    async def edit_message(self, **kw):
        self.edits.append(kw)

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def defer(self, **kw):
        self.deferred = True

    async def send(self, content=None, **kw):
        self.sent.append((content, kw))

    def is_done(self):
        return bool(self.sent or self.edits or self.modals or self.deferred)


def make_interaction(user, custom_id="", message=None, channel=None):
    rec = Recorder()
    return SimpleNamespace(
        user=user, data={"custom_id": custom_id}, message=message, channel=channel,
        response=rec, followup=rec, guild=SimpleNamespace(id=1), guild_id=1,
    )


# --- storage --------------------------------------------------------------------

def test_appeals_votes_and_tickets_are_stored():
    store = CaseStore(":memory:")
    case = store.create_case(1, 2, "scam", ["x"])
    assert store.create_appeal(case.id, 2, "it was a joke")
    assert store.create_appeal(case.id, 2, "again") is None  # one appeal per case

    store.record_answer(10, 1, 5, 2, "how do I X?", "like this")
    assert store.vote(10, 3, -1) == -1
    assert store.vote(10, 4, -1) == -1
    assert store.vote(10, 3, -1) == 0  # the same vote again takes it back
    assert store.vote(10, 3, 1) == 1  # or a member can change their mind
    assert store.vote_counts(10) == (1, 1)
    assert store.downvoted_answers(1) == []  # one up, one down: not "unhelpful"
    store.vote(10, 5, -1)
    assert [a.question for a in store.downvoted_answers(1)] == ["how do I X?"]
    assert store.downvoted_answers(99) == []  # other servers' answers aren't shown

    ticket = store.create_ticket(77, 1, 2, "help me")
    assert store.open_ticket_for(1, 2).thread_id == ticket.thread_id
    store.set_ticket_status(77, "closed")
    assert store.open_ticket_for(1, 2) is None


def test_which_cases_can_be_appealed():
    store = CaseStore(":memory:")
    case = store.create_case(1, 2, "scam", ["x"])
    assert appeals.appealable(case)
    case.status, case.decision = "closed", "timeout lifted by Mod, warning kept"
    assert appeals.appealable(case)
    case.decision = "banned by Mod"
    assert not appeals.appealable(case)
    case.decision = "not a violation (dismissed by Mod), warning removed"
    assert not appeals.appealable(case)
    assert appeals.parse_custom_id("appeal:5:open") == (5, "open")
    assert appeals.parse_custom_id("appeal:5:delete") is None
    assert appeals.parse_custom_id("case:5:ban") is None


# --- feedback buttons -------------------------------------------------------------

def test_feedback_votes_update_the_counts_on_the_buttons():
    store = CaseStore(":memory:")
    store.record_answer(10, 1, 5, 2, "q", "a")
    message = SimpleNamespace(id=10)

    async def go():
        for user, button in ((3, community.FEEDBACK_UP), (4, community.FEEDBACK_UP), (5, community.FEEDBACK_DOWN)):
            interaction = make_interaction(make_member(user), button, message)
            await community.handle_feedback(store, interaction)
        old = make_interaction(make_member(6), community.FEEDBACK_UP, SimpleNamespace(id=999))
        await community.handle_feedback(store, old)
        return old

    old = run(go())
    assert store.vote_counts(10) == (2, 1)
    assert "too old" in old.response.sent[0][0]
    labels = [b.label for b in community.feedback_view(2, 1).children]
    assert labels == ["2", "1"]


# --- help threads ------------------------------------------------------------------

def test_help_thread_helpers():
    assert community.thread_name("Sam O'Neil", "How do I recompile @everyone?!") == "help-sam-oneil-how-do-i-recompile-everyone"
    assert len(community.thread_name("x" * 200, "y" * 200)) <= 90
    assert community.parse_ticket_id("tk:123:close") == (123, "close")
    assert community.parse_ticket_id("tk:abc:close") is None
    store = CaseStore(":memory:")
    ticket = store.create_ticket(7, 1, 2, "t")
    assert community.bot_answers_in(ticket, 2)
    assert not community.bot_answers_in(ticket, 3)  # only the member who opened it
    store.set_ticket_status(7, "human")
    assert not community.bot_answers_in(store.get_ticket(7), 2)  # a moderator took over
    assert not community.bot_answers_in(None, 2)


def test_ticket_buttons_close_and_get_a_human():
    store = CaseStore(":memory:")
    store.create_ticket(7, 1, 2, "recompile help")
    sent, notes = [], []

    async def thread_send(text, **kw):
        sent.append(text)

    async def thread_edit(**kw):
        sent.append(("edit", kw))

    async def notify(text):
        notes.append(text)

    thread = SimpleNamespace(send=thread_send, edit=thread_edit, mention="<#7>")
    bot = SimpleNamespace(store=store, settings=SimpleNamespace(help_staff_role_id=555), notify_mods=notify)

    async def go():
        stranger = make_interaction(make_member(9), "tk:7:close", channel=thread)
        await community.handle_ticket_button(bot, stranger)
        human = make_interaction(make_member(2), "tk:7:human", channel=thread)
        await community.handle_ticket_button(bot, human)
        again = make_interaction(make_member(2), "tk:7:human", channel=thread)
        await community.handle_ticket_button(bot, again)
        close = make_interaction(make_member(2), "tk:7:close", channel=thread)
        await community.handle_ticket_button(bot, close)
        return stranger, again

    stranger, again = run(go())
    assert "Only the member" in stranger.response.sent[0][0]
    assert any("<@&555>" in s for s in sent if isinstance(s, str))  # the staff role was pinged
    assert "already asked" in again.response.sent[0][0]
    assert notes and "recompile help" in notes[0]
    assert store.get_ticket(7).status == "closed"
    assert ("edit", {"archived": True, "locked": True}) in sent


# --- appeals -------------------------------------------------------------------------

def make_appeal_env():
    store = CaseStore(":memory:")
    case = store.create_case(1, 2, "scam", ["posted a scam link"])
    posted, dms, refreshed = [], [], []

    async def channel_send(**kw):
        posted.append(kw)
        return SimpleNamespace(id=4242)

    async def dm_send(text):
        dms.append(text)

    class Cases:
        settings = SimpleNamespace(appeals_enabled=True)

        def log_channel(self):
            return SimpleNamespace(id=3, send=channel_send)

        async def refresh_report(self, c):
            refreshed.append(c.id)

        async def dismiss(self, c, guild, moderator):
            store.revoke_warnings_for_case(c.id)
            c.status, c.decision = "closed", "not a violation (dismissed), warning removed"
            store.save(c)
            return f"Case #{c.id}: dismissed and warning removed."

    client = SimpleNamespace(
        get_user=lambda uid: SimpleNamespace(send=dm_send), get_guild=lambda gid: SimpleNamespace(name="Test Server")
    )
    return store, case, appeals.AppealManager(client, store, Cases()), posted, dms, refreshed


def test_member_appeal_reaches_moderators_once():
    store, case, manager, posted, dms, refreshed = make_appeal_env()

    async def go():
        stranger = make_interaction(make_member(9), f"appeal:{case.id}:open")
        await manager.handle_button(stranger)
        owner = make_interaction(make_member(2), f"appeal:{case.id}:open")
        await manager.handle_button(owner)
        await manager.submit(owner, case.id, "I was quoting the scam to warn people")
        again = make_interaction(make_member(2), f"appeal:{case.id}:open")
        await manager.handle_button(again)
        return stranger, owner, again

    stranger, owner, again = run(go())
    assert "isn't yours" in stranger.response.sent[0][0]
    assert len(owner.response.modals) == 1  # the form opened for the member
    assert len(posted) == 1 and posted[0]["view"] is not None  # moderators got Accept / Reject
    assert store.get_appeal(case.id).text.startswith("I was quoting")
    assert "waiting for a moderator" in again.response.sent[0][0]
    assert refreshed == [case.id]


def test_moderators_accept_or_reject_appeals():
    store, case, manager, posted, dms, refreshed = make_appeal_env()
    other = store.create_case(1, 2, "piracy", ["asked for an ISO"])
    store.create_appeal(case.id, 2, "mistake")
    store.create_appeal(other.id, 2, "mistake")

    async def edit(**kw):
        pass

    async def go():
        nobody = make_interaction(make_member(8), f"appeal:{case.id}:accept", message=SimpleNamespace(edit=edit))
        await manager.handle_button(nobody)  # no Moderate Members
        mod = make_member(8, moderate_members=True)
        accept = make_interaction(mod, f"appeal:{case.id}:accept", message=SimpleNamespace(edit=edit))
        await manager.handle_button(accept)
        reject = make_interaction(mod, f"appeal:{other.id}:reject", message=SimpleNamespace(edit=edit))
        await manager.handle_button(reject)
        twice = make_interaction(mod, f"appeal:{case.id}:reject", message=SimpleNamespace(edit=edit))
        await manager.handle_button(twice)
        return nobody, twice

    nobody, twice = run(go())
    assert "moderate members" in nobody.response.sent[0][0]
    assert store.get_appeal(case.id).status == "accepted"
    assert store.get(case.id).status == "closed"
    assert store.get_appeal(other.id).status == "rejected"
    assert store.get(other.id).is_open  # a rejected appeal leaves the case as it was
    assert "already accepted" in twice.response.sent[0][0]
    assert any("accepted your appeal" in d for d in dms) and any("warning stays" in d for d in dms)


# --- welcome, intents, permissions ------------------------------------------------------

def test_welcome_template():
    text = community.render_welcome("Hi {user}, welcome to {server}! Read {rules}. {help} {unknown}", "<@1>", "Recomp", "<#9>")
    assert text == "Hi <@1>, welcome to Recomp! Read <#9>. `/help` {unknown}"
    assert len(community.render_welcome("x" * 5000, "", "", "")) <= 1900
    assert "{user}" in config.DEFAULT_WELCOME and "{rules}" in config.DEFAULT_WELCOME


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "k")
    monkeypatch.setenv("MOD_DB_PATH", str(tmp_path / "mod.sqlite3"))
    monkeypatch.delenv("WELCOME_CHANNEL_ID", raising=False)


def test_members_intent_is_only_requested_for_a_welcome_message(env, monkeypatch):
    from bot.main import REQUIRED_PERMISSIONS, AssistantBot

    assert not AssistantBot(config.load_settings()).intents.members  # nothing privileged without a welcome
    monkeypatch.setenv("WELCOME_CHANNEL_ID", "1564508428590776381")
    settings = config.load_settings()
    assert AssistantBot(settings).intents.members
    assert not AssistantBot(settings, members=False).intents.members  # the fallback when the portal says no
    assert REQUIRED_PERMISSIONS.create_private_threads and REQUIRED_PERMISSIONS.manage_threads


def test_new_settings_are_reachable_from_the_settings_page():
    from bot import runtime_settings as rs

    for key in ("WELCOME_CHANNEL_ID", "WELCOME_MESSAGE", "HELP_ENABLED", "HELP_CHANNEL_ID", "HELP_STAFF_ROLE_ID", "APPEALS_ENABLED"):
        assert key in rs.FIELDS
    assert rs.validate(rs.FIELDS["APPEALS_ENABLED"], "false") == "false"


# --- modes are opt-in and explained ---------------------------------------------------

def test_appeals_and_help_are_off_until_a_moderator_turns_them_on(env):
    settings = config.load_settings()
    assert not settings.appeals_enabled and not settings.help_enabled
    assert not settings.welcome_ai and settings.welcome_channel_id is None


def test_appeals_button_stops_working_when_the_mode_is_off():
    store, case, manager, posted, dms, refreshed = make_appeal_env()
    manager.cases.settings = SimpleNamespace(appeals_enabled=False)

    async def go():
        owner = make_interaction(make_member(2), f"appeal:{case.id}:open")
        await manager.handle_button(owner)
        return owner

    owner = run(go())
    assert "turned off" in owner.response.sent[0][0] and not owner.response.modals


def test_each_mode_is_explained_to_moderators():
    from bot import runtime_settings as rs, settings_ui

    for key in ("APPEALS_ENABLED", "HELP_ENABLED", "WELCOME_AI"):
        field = rs.FIELDS[key]
        assert field.group == "Community" and field.kind == "bool" and len(field.about) > 150
        assert not field.admin  # moderators (Manage Server) can decide
    assert "off" in rs.GROUP_NOTES["Community"] and "Appeals mode" in rs.GROUP_NOTES["Community"]
    assert "Manage Threads" in rs.FIELDS["HELP_ENABLED"].about

    bot = SimpleNamespace(settings_pending=set(), min_confidence=0.75, chat_context=8)

    async def build():
        embed, _ = settings_ui.page(bot, "Community")
        assert "Help mode" in embed.description and "Welcome" in embed.description
        view = settings_ui.ChoiceView(bot, rs.FIELDS["APPEALS_ENABLED"])
        assert view.children[0].options

    asyncio.run(build())
    assert "Appeal" in settings_ui._enable_note(rs.FIELDS["APPEALS_ENABLED"], "true")
    assert "Create Private Threads" in settings_ui._enable_note(rs.FIELDS["HELP_ENABLED"], "true")
    assert settings_ui._enable_note(rs.FIELDS["HELP_ENABLED"], "false") == ""


# --- AI welcome ---------------------------------------------------------------------------

def make_welcome_bot(reply="Welcome aboard! Read <#9>.", fail=False, help_enabled=False):
    calls = []

    class AI:
        async def complete(self, messages):
            calls.append(messages)
            if fail:
                raise RuntimeError("provider down")
            return reply

    guild = SimpleNamespace(id=1, name="Recomp", me=SimpleNamespace(display_name="Bot"))
    member = SimpleNamespace(mention="<@5>", guild=guild)
    bot = SimpleNamespace(
        ai=AI(), last_ai_welcome=-1e9, rules=SimpleNamespace(get=lambda gid: "Be kind."),
        settings=SimpleNamespace(system_prompt="", community_about="static recompilation", help_enabled=help_enabled),
    )
    return bot, member, calls


def test_ai_welcome_is_rate_limited_and_falls_back_to_the_moderators_message():
    bot, member, calls = make_welcome_bot(reply="Hi all @everyone and @here, welcome!")
    first = run(community.ai_welcome(bot, member, "<#9>"))
    assert first.startswith("<@5> ") and "@everyone" not in first and "@here" not in first
    system = " ".join(m.content for m in calls[0] if m.role == "system")
    assert "<#9>" in calls[0][-1].content and "Be kind." in system and "static recompilation" in system
    assert run(community.ai_welcome(bot, member, "<#9>")) is None  # too soon: use the fixed message

    failing, member, _ = make_welcome_bot(fail=True)
    assert run(community.ai_welcome(failing, member, "<#9>")) is None
    empty, member, _ = make_welcome_bot(reply="   ")
    assert run(community.ai_welcome(empty, member, "<#9>")) is None
    helping, member, calls = make_welcome_bot(help_enabled=True)
    run(community.ai_welcome(helping, member, "<#9>"))
    assert "/help" in calls[0][-1].content
