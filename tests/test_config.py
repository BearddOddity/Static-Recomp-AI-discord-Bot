import pytest

from bot import config
from bot.ai import build_provider
from bot.ai.openai_compat import OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Keep a developer's real .env out of these tests.
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in [
        "AI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_COMPAT_API_KEY",
        "OPENAI_COMPAT_MODEL",
        "OPENAI_COMPAT_BASE_URL",
        "AI_CLI_COMMAND",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "token")


def test_missing_provider_key_fails_at_startup(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        config.load_settings()


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="AI_PROVIDER must be one of"):
        config.load_settings()


def test_free_preset_builds_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "key")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "some/model:free")
    provider = build_provider(config.load_settings())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._base_url == "https://openrouter.ai/api/v1"


def test_template_defaults_are_generic_and_delete_on_warning(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    for name in ("SERVER_PROFILE", "COMMUNITY_ABOUT", "AI_SYSTEM_PROMPT", "DOCS_REPOS", "MOD_ACTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_SYSTEM_PROMPT", "")  # "AI_SYSTEM_PROMPT=" in .env means the default
    settings = config.load_settings()
    assert settings.community_about == "video games"
    assert settings.system_prompt == ""  # the built-in personality
    assert settings.assistant_channel_ids == frozenset() and settings.ask_on_mention
    assert settings.docs_repos == ""
    assert settings.mod_action == settings.mod_piracy_action == settings.mod_rule_action == "delete"
    assert not settings.mod_severe_scam_review


def test_missing_server_profile_fails_at_startup(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("SERVER_PROFILE", "servers/nope/server.env")
    with pytest.raises(RuntimeError, match="SERVER_PROFILE"):
        config.load_settings()


def test_classifier_prompt_names_the_community():
    from bot.moderation.classifier import system_prompt

    prompt = system_prompt("retro handhelds")
    assert "Discord server about retro handhelds." in prompt and '{"label"' in prompt
    assert "recomp" not in system_prompt().lower()
