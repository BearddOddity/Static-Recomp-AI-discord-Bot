import pytest

from bot.docs import DocsIndex, DocsRouter, Document

ROUTES = "switch 2,switch2,ns2=example/switch2-tools; switch=example/switch-tools; ps3=example/ps3-tools"
SWITCH2, SWITCH, PS3 = "example/switch2-tools", "example/switch-tools", "example/ps3-tools"


@pytest.fixture
def router():
    return DocsRouter.parse(ROUTES)


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How do I mod a Switch game?", [SWITCH]),
        ("switch's file system?", [SWITCH]),
        ("Does this work for Switch 2 titles?", [SWITCH2]),
        ("switch2 shaders", [SWITCH2]),
        ("NS2 save files", [SWITCH2]),
        ("PS3 SPU support", [PS3]),
        ("Is Switch or PS3 easier?", [SWITCH, PS3]),
        ("Switch 2 vs original Switch", [SWITCH2, SWITCH]),
        ("How do mods work?", []),
        ("switchboard", []),
        ("ps30 frames", []),
    ],
)
def test_keyword_routing(router, question, expected):
    assert router.match(question) == expected


def test_bad_rule_is_rejected():
    with pytest.raises(ValueError):
        DocsRouter.parse("switch=notarepo")
    assert DocsRouter.parse("").match("switch") == []


def test_search_can_be_limited_to_repos():
    index = DocsIndex(
        [
            Document(SWITCH, "README.md", "u1", "# Install\nInstall the toolchain."),
            Document(PS3, "README.md", "u2", "# Install\nInstall the toolchain and SDK."),
        ]
    )
    assert {c.document.repo for c in index.search("install")} == {SWITCH, PS3}
    assert [c.document.repo for c in index.search("install", repos=[SWITCH])] == [SWITCH]
