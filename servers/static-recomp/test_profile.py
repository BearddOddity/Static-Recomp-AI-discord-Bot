"""Checks this server's profile. Delete the whole servers/static-recomp folder
when you start your own bot from the template."""

from pathlib import Path

import pytest
from dotenv import dotenv_values

from bot import config
from bot.docs import DocsRouter, parse_repo_list

PROFILE = Path(__file__).with_name("server.env")
REX, XBOX, PS3 = "rexglue/rexglue-sdk", "sp00nznet/xboxrecomp", "sp00nznet/ps3recomp"


@pytest.fixture
def values():
    return dotenv_values(PROFILE)


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How do I recompile an Xbox game?", [XBOX]),
        ("Does this work for Xbox 360 titles?", [REX]),
        ("what about 360 games", [REX]),
        ("PS3 SPU support", [PS3]),
        ("Xbox 360 vs original Xbox", [REX, XBOX]),
        ("3600 frames", []),
    ],
)
def test_keyword_routing(values, question, expected):
    assert DocsRouter.parse(values["DOCS_ROUTES"]).match(question) == expected


def test_every_route_points_at_a_listed_repo(values):
    repos = {s.base_name.lower() for s in parse_repo_list(values["DOCS_REPOS"])}
    assert DocsRouter.parse(values["DOCS_ROUTES"]).repos <= repos


def test_profile_fills_gaps_but_env_wins(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("SERVER_PROFILE", str(PROFILE))
    monkeypatch.setenv("DOCS_ROUTES", "")  # empty in .env still means "use the profile"
    monkeypatch.setenv("OPENROUTER_APP_NAME", "My own name")
    for name in ("COMMUNITY_ABOUT", "DOCS_REPOS", "AI_SYSTEM_PROMPT"):
        monkeypatch.setenv(name, "")  # so monkeypatch restores what the profile sets
    settings = config.load_settings(require_discord=False)
    assert "static recompilation" in settings.community_about
    assert REX in settings.docs_routes and XBOX in settings.docs_repos
    assert settings.openrouter_app_name == "My own name"
