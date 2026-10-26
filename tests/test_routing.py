import pytest

from bot.docs import DocsIndex, DocsRouter, Document

ROUTES = (
    "xbox 360,xbox360,x360,360=rexglue/rexglue-sdk; "
    "xbox=sp00nznet/xboxrecomp; ps3=sp00nznet/ps3recomp"
)
REX, XBOX, PS3 = "rexglue/rexglue-sdk", "sp00nznet/xboxrecomp", "sp00nznet/ps3recomp"


@pytest.fixture
def router():
    return DocsRouter.parse(ROUTES)


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How do I recompile an Xbox game?", [XBOX]),
        ("xbox's kernel imports?", [XBOX]),
        ("Does this work for Xbox 360 titles?", [REX]),
        ("xbox360 shaders", [REX]),
        ("X360 save files", [REX]),
        ("what about 360 games", [REX]),
        ("PS3 SPU support", [PS3]),
        ("Is Xbox or PS3 easier?", [XBOX, PS3]),
        ("Xbox 360 vs original Xbox", [REX, XBOX]),
        ("How do mods work?", []),
        ("xboxone", []),
        ("3600 frames", []),
    ],
)
def test_keyword_routing(router, question, expected):
    assert router.match(question) == expected


def test_bad_rule_is_rejected():
    with pytest.raises(ValueError):
        DocsRouter.parse("xbox=notarepo")
    assert DocsRouter.parse("").match("xbox") == []


def test_search_can_be_limited_to_repos():
    index = DocsIndex(
        [
            Document(XBOX, "README.md", "u1", "# Install\nInstall the toolchain."),
            Document(PS3, "README.md", "u2", "# Install\nInstall the toolchain and SDK."),
        ]
    )
    assert {c.document.repo for c in index.search("install")} == {XBOX, PS3}
    assert [c.document.repo for c in index.search("install", repos=[XBOX])] == [XBOX]
