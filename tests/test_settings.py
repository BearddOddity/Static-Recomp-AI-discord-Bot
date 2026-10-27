import asyncio
import os
from types import SimpleNamespace

import pytest

from bot import config, runtime_settings as rs


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("MOD_DB_PATH", str(tmp_path / "mod.sqlite3"))
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "env-key-12345")
    monkeypatch.delenv("MOD_LOG_CHANNEL_ID", raising=False)
    yield
    rs.revert_overrides()
    rs.rejected_reason = ""


def test_dangerous_settings_cannot_be_changed_from_discord():
    assert "DISCORD_TOKEN" not in rs.FIELDS  # a wrong token would lock everyone out
    assert "AI_CLI_COMMAND" not in rs.FIELDS  # it runs a command on the server
    assert "cli" not in rs.FIELDS["AI_PROVIDER"].choices
    assert all(f.admin for f in rs.fields_in("Keys") if f.secret)


def test_validation():
    key = rs.FIELDS["OPENAI_COMPAT_API_KEY"]
    assert rs.validate(key, "  sk-or-v1-abcdef123  ") == "sk-or-v1-abcdef123"
    for bad in ("short", "has space in it 123", 'quote"d-key-12345', "x" * 401):
        with pytest.raises(ValueError):
            rs.validate(key, bad)
    with pytest.raises(ValueError):
        rs.validate(key, "")  # an empty key is a cancel, never a clear

    channel = rs.FIELDS["MOD_LOG_CHANNEL_ID"]
    assert rs.validate(channel, "1564508428590776381") == "1564508428590776381"
    assert rs.validate(channel, "") == ""  # optional settings can be cleared
    with pytest.raises(ValueError):
        rs.validate(channel, "#mod-log")
    assert rs.validate(rs.FIELDS["MOD_EXEMPT_ROLE_IDS"], "111111111111111111, 222222222222222222") == (
        "111111111111111111,222222222222222222"
    )

    assert rs.validate(rs.FIELDS["MOD_WARN_LIMIT"], "5") == "5"
    for bad in ("0", "21", "many"):
        with pytest.raises(ValueError):
            rs.validate(rs.FIELDS["MOD_WARN_LIMIT"], bad)
    assert rs.validate(rs.FIELDS["MOD_ENABLED"], "TRUE") == "true"
    assert rs.validate(rs.FIELDS["MOD_ACTION"], "Flag") == "flag"
    with pytest.raises(ValueError):
        rs.validate(rs.FIELDS["MOD_ACTION"], "ban")  # the bot never bans
    with pytest.raises(ValueError):
        rs.validate(rs.FIELDS["AI_PROVIDER"], "")
    assert rs.validate(rs.FIELDS["AI_BACKUP_PROVIDER"], "") == ""
    with pytest.raises(ValueError):
        rs.validate(rs.FIELDS["OPENAI_COMPAT_BASE_URL"], "http://evil.example/v1")
    with pytest.raises(ValueError):
        rs.validate(rs.FIELDS["COMMUNITY_ABOUT"], "line one\nline two")
    assert rs.validate(rs.FIELDS["AI_SYSTEM_PROMPT"], "line one\nline two") == "line one\nline two"


def test_secrets_are_never_displayed():
    key = rs.FIELDS["OPENAI_COMPAT_API_KEY"]
    assert rs.display(key, "sk-or-v1-supersecret") == "✅ set"
    assert "supersecret" not in rs.display(key, "sk-or-v1-supersecret")
    assert rs.display(key, "") == "not set"


def test_saved_settings_win_over_env_and_can_be_reverted(monkeypatch):
    monkeypatch.setenv("MOD_LOG_CHANNEL_ID", "111111111111111111")
    rs.save_overrides({"MOD_LOG_CHANNEL_ID": "222222222222222222", "OPENAI_COMPAT_API_KEY": "saved-key-12345"})
    settings = config.load_settings()
    assert settings.mod_log_channel_id == 222222222222222222
    assert settings.openai_compat_api_key == "saved-key-12345"
    rs.revert_overrides()
    assert os.environ["MOD_LOG_CHANNEL_ID"] == "111111111111111111"
    assert os.environ["OPENAI_COMPAT_API_KEY"] == "env-key-12345"


def test_unknown_or_live_settings_in_the_file_are_ignored(tmp_path):
    rs.save_overrides({"DISCORD_TOKEN": "stolen", "MOD_MIN_CONFIDENCE": "0.9", "MOD_ACTION": "flag", "X": 1})
    assert rs.load_overrides() == {"MOD_ACTION": "flag"}


def test_bad_saved_settings_are_set_aside_instead_of_locking_the_bot_out(monkeypatch):
    rs.save_overrides({"AI_PROVIDER": "openrouter", "OPENAI_COMPAT_API_KEY": "saved-key-12345", "MOD_WARN_LIMIT": "zzz"})
    with pytest.raises(ValueError):
        config.load_settings()
    assert rs.reject_overrides("bad warn limit")
    assert rs.settings_path().with_name("settings.rejected.json").is_file()
    assert not rs.settings_path().exists()
    assert config.load_settings().openai_compat_api_key == "env-key-12345"  # back to .env
    assert rs.rejected_reason == "bad warn limit"
    assert not rs.reject_overrides("again")  # nothing saved: the problem is in .env itself


def test_settings_pages_fit_discords_limits():
    from bot import settings_ui

    bot = SimpleNamespace(settings_pending=set(), min_confidence=0.75, chat_context=8)

    async def build():
        embed, view = settings_ui.page(bot, None)
        assert len(embed.description) < 4000
        for group in rs.GROUPS:
            embed, view = settings_ui.page(bot, group)
            assert len(embed.description) < 4000
            select = view.children[0]
            assert 1 <= len(select.options) <= 25
            for option in select.options:
                assert 1 <= len(option.label) <= 100 and 1 <= len(option.description) <= 100
        for field in rs.FIELDS.values():
            if field.kind in ("choice", "bool"):
                assert settings_ui.ChoiceView(bot, field).children[0].options
            elif not field.live:
                settings_ui.FieldModal(bot, field, 1)  # builds without exceeding form limits

    asyncio.run(build())


def test_saving_a_key_never_leaks_it_into_the_page_or_the_mod_log():
    from bot import settings_ui

    sent, notes, edits = [], [], []

    async def notify(text):
        notes.append(text)

    async def edit_message(**kwargs):
        edits.append(kwargs)

    bot = SimpleNamespace(
        settings_pending=set(), min_confidence=0.75, chat_context=8, notify_mods=notify,
        set_tuning=lambda **kw: sent.append(kw),
    )
    guild = SimpleNamespace(get_channel=lambda i: object() if i == 1564508428590776381 else None)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7, mention="<@7>"), guild=guild,
        response=SimpleNamespace(edit_message=edit_message),
    )
    secret = "sk-or-v1-TOPSECRET123456"

    async def run():
        key = rs.FIELDS["OPENAI_COMPAT_API_KEY"]
        await settings_ui.save_and_show(bot, interaction, key, secret, 7)
        await settings_ui.save_and_show(bot, interaction, rs.FIELDS["MOD_LOG_CHANNEL_ID"], "1564508428590776381", 7)
        await settings_ui.save_and_show(bot, interaction, rs.FIELDS["MOD_LOG_CHANNEL_ID"], "999999999999999999", 7)
        await settings_ui.save_and_show(bot, interaction, rs.FIELDS["MOD_MIN_CONFIDENCE"], "0.85", 7)

    asyncio.run(run())
    assert rs.load_overrides()["OPENAI_COMPAT_API_KEY"] == secret  # stored for the next start
    assert rs.load_overrides()["MOD_LOG_CHANNEL_ID"] == "1564508428590776381"
    assert not any("TOPSECRET" in n for n in notes)
    assert all("TOPSECRET" not in (e["embed"].description or "") for e in edits)
    assert any("value hidden" in n for n in notes)
    assert any("can't see a channel" in (e["embed"].description or "") for e in edits)  # unknown channel refused
    assert sent == [{"confidence": 0.85}]  # live setting goes to /tune's store, not the file
    assert bot.settings_pending == {"OPENAI_COMPAT_API_KEY", "MOD_LOG_CHANNEL_ID"}
