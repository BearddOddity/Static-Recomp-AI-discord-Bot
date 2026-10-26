import asyncio
import json
from pathlib import Path
import httpx
import pytest

from bot.ai import AIProvider, Image, Message
from bot.ai.anthropic_provider import AnthropicProvider
from bot.ai.ollama import OllamaProvider
from bot.ai.openai_compat import OpenAICompatibleProvider
from bot.moderation import Classifier, LinkChecker, Moderator, Post, extract_urls
from bot.moderation.classifier import parse_verdict
from bot.moderation.text import check_text

EXAMPLES = sorted(Path(__file__).parent.parent.joinpath("examples", "scam").glob("*.webp"))
FAKE_TWEET = (
    "I am pleased to announce the launch of my own cryptocurrency casino! To celebrate, I am "
    "giving away $5,600 to everyone who registers, and you can withdraw the bonus immediately. "
    "Go to: kegwin.com Enter the special promo code: BET. This post will be deleted an hour "
    "after publication so that only the fastest people will find out. - MrBeast"
)


def run(coro):
    return asyncio.run(coro)


class FakeAI(AIProvider):
    name = "fake"
    supports_images = True

    def __init__(self, reply='{"label": "scam", "confidence": 0.95, "reasons": ["fake giveaway"]}'):
        self.reply = reply
        self.calls: list[list[Message]] = []

    async def complete(self, messages):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def moderator(ai: AIProvider | None = None, **kwargs) -> Moderator:
    return Moderator(LinkChecker(), Classifier(ai) if ai else None, **kwargs)


def example_images() -> list[Image]:
    return [Image(p.read_bytes(), "image/webp") for p in EXAMPLES]


# --- links -----------------------------------------------------------------


def test_extract_urls_finds_full_and_bare_links_only():
    text = "go to kegwin.com or https://bit.ly/x?a=1. run python -m bot.main, mail me@site.com"
    assert extract_urls(text) == ["https://bit.ly/x?a=1", "kegwin.com"]


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://kegwin.com/promo", "known scam domain kegwin.com"),
        ("https://app.kegwin.com", "known scam domain app.kegwin.com"),
        ("https://dlscord-gift.com", "dlscord-gift.com imitates discord"),
        ("https://steamcommunlty.com/trade", "steamcommunlty.com imitates steamcommunity"),
        ("https://steam-gift.ru", "steam-gift.ru imitates steam"),
        ("https://bit.ly/abc", "link shortener hides the destination (bit.ly)"),
        ("http://xn--dscord-6ve.com", "punycode domain that can imitate another xn--dscord-6ve.com"),
    ],
)
def test_suspicious_links(url, expected):
    assert expected in [s.reason for s in LinkChecker().check([url])]


@pytest.mark.parametrize(
    "url",
    ["https://github.com/rexglue/rexglue-sdk", "https://livestream.com/x", "https://myteam.net", "https://discord.com/channels/1/2"],
)
def test_normal_links_add_no_risk(url):
    assert LinkChecker().check([url]) == []


def test_extra_blocked_and_allowed_domains():
    checker = LinkChecker(extra_blocked={"evil.example"}, extra_allowed={"rexglue-fans.xyz"})
    assert checker.check(["https://evil.example"])[0].weight == 100
    assert checker.check(["https://rexglue-fans.xyz"]) == []


# --- text --------------------------------------------------------------------


def test_fake_celebrity_casino_text_is_recognised():
    reasons = {s.reason for s in check_text(FAKE_TWEET)}
    assert {
        "crypto giveaway, casino or bonus promotion",
        "celebrity name used to promote money or crypto",
        "asks people to enter a promo or bonus code",
        "pressure to act fast",
    } <= reasons


@pytest.mark.parametrize(
    "text",
    [
        "The recompiler frees memory after each function, see the wiki",
        "How do I dump my own Xbox 360 disc for rexglue?",
        "Is this kegwin thing a scam? my friend got a DM",
    ],
)
def test_normal_chat_has_no_text_signals(text):
    assert check_text(text) == []


def test_piracy_request_is_noticed():
    assert check_text("anyone have a mega link for the ps3 iso?")


# --- engine ------------------------------------------------------------------


def test_blocklisted_domain_is_scam_without_ai():
    ai = FakeAI()
    verdict = run(moderator(ai).review(Post(FAKE_TWEET, 1, 1)))
    assert verdict.label == "scam" and verdict.source == "rules"
    assert ai.calls == []


def test_scam_screenshots_with_no_text_go_to_the_ai_with_images():
    ai = FakeAI()
    images = example_images()
    assert len(images) == 4
    verdict = run(moderator(ai).review(Post("", 1, 1, images=images, attachment_count=4, account_age_days=900)))
    assert verdict.label == "scam" and verdict.source == "ai"
    sent = ai.calls[0][1]
    assert len(sent.images) == 4 and "(no text)" in sent.content


def test_identical_repost_uses_cache_not_a_second_ai_call():
    ai = FakeAI()
    mod = moderator(ai)
    images = example_images()
    first = run(mod.review(Post("", 1, 1, images=images, attachment_count=4)))
    second = run(mod.review(Post("", 2, 2, images=images, attachment_count=4)))
    assert first.flagged and second.flagged and second.source == "cache"
    assert len(ai.calls) == 1


def test_single_screenshot_from_regular_member_skips_the_ai():
    ai = FakeAI()
    post = Post("look, it boots!", 1, 1, images=example_images()[:1], attachment_count=1,
                account_age_days=800, member_age_hours=5000)
    assert not run(moderator(ai).review(post)).flagged
    assert ai.calls == []


def test_low_confidence_or_ok_ai_verdicts_do_not_flag():
    images = example_images()
    unsure = FakeAI('{"label": "scam", "confidence": 0.4, "reasons": []}')
    assert not run(moderator(unsure).review(Post("", 1, 1, images=images, attachment_count=4))).flagged
    fine = FakeAI('Sure! {"label": "ok", "confidence": 0.9, "reasons": ["game screenshot"]}')
    assert not run(moderator(fine).review(Post("", 1, 1, images=images, attachment_count=4))).flagged


def test_ai_failure_falls_back_to_rules():
    ai = FakeAI(RuntimeError("rate limited"))
    text = "Free Discord Nitro, claim now at https://dlscord-gift.com"
    verdict = run(moderator(ai).review(Post(text, 1, 1)))
    assert verdict.label == "scam" and verdict.source == "rules"


def test_same_post_in_three_channels_is_flagged():
    mod = moderator(None)
    post = lambda channel, t: Post("hey check this https://unknown-site.example/x", 7, channel, created_at=t)
    assert not run(mod.review(post(1, 0))).flagged
    assert not run(mod.review(post(2, 10))).flagged
    verdict = run(mod.review(post(3, 20)))
    assert verdict.flagged
    assert any("3 channels" in r for r in verdict.reasons)


def test_ai_scan_off_never_calls_the_ai():
    ai = FakeAI()
    run(moderator(ai, ai_scan="off").review(Post("", 1, 1, images=example_images(), attachment_count=4)))
    assert ai.calls == []


def test_parse_verdict_handles_bad_replies():
    assert parse_verdict("no json here") is None
    assert parse_verdict('{"label": "spam", "confidence": 1}') is None
    verdict = parse_verdict('```json\n{"label": "PIRACY", "confidence": "2", "reasons": "iso link"}\n```')
    assert verdict.label == "piracy" and verdict.confidence == 1.0 and verdict.reasons == ("iso link",)


# --- providers send images in their own formats --------------------------------


def _capture(provider_cls, *args, reply):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=reply)

    provider = provider_cls(*args, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    image = Image(b"\x89PNG", "image/png")
    run(provider.complete([Message("user", "is this a scam?", images=(image,))]))
    return seen["body"]["messages"][0]


def test_anthropic_sends_base64_image_blocks():
    msg = _capture(AnthropicProvider, "k", "m", reply={"content": [{"type": "text", "text": "{}"}]})
    assert msg["content"][0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw=="}}
    assert msg["content"][1] == {"type": "text", "text": "is this a scam?"}


def test_openai_compatible_sends_data_url_images():
    msg = _capture(OpenAICompatibleProvider, "https://x.test/v1", "k", "m", reply={"choices": [{"message": {"content": "{}"}}]})
    assert msg["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw=="}}


def test_ollama_sends_images_field():
    msg = _capture(OllamaProvider, "http://localhost:11434", "llava", reply={"message": {"content": "{}"}})
    assert msg["images"] == ["iVBORw=="]


# --- anti-piracy -------------------------------------------------------------------

from bot.moderation.files import check_attachments, check_urls, zip_member_names  # noqa: E402


@pytest.mark.parametrize(
    "name",
    ["Halo 3.iso", "default.xex", "game.XBE", "boot.elf", "EBOOT.BIN", "mario.z64", "zelda.nsp",
     "disc.chd", "game.rvz", "license.rap", "game.pkg"],
)
def test_game_file_attachments_are_certain_piracy(name):
    verdict = run(moderator(FakeAI()).review(Post("here you go", 1, 1, attachment_count=1, attachment_names=[name])))
    assert verdict.label == "piracy" and verdict.source == "rules"
    assert any(name in r for r in verdict.reasons)


@pytest.mark.parametrize("name", ["crash.log", "build.zip", "notes.md", "screenshot.png", "app.exe", "patch.toml"])
def test_normal_attachments_are_fine(name):
    assert check_attachments([name]) == []


def test_bin_counts_only_next_to_a_cue():
    assert check_attachments(["track01.bin"])[0].weight < 100
    assert {s.weight for s in check_attachments(["game.cue", "track01.bin"])} == {100}


def test_game_files_inside_a_zip_are_found(tmp_path):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hi")
        archive.writestr("Game/default.xex", b"XEX2")
    names = ["totally-legit.zip"] + zip_member_names("totally-legit.zip", buffer.getvalue())
    assert names[-1] == "totally-legit.zip/Game/default.xex"
    assert check_attachments(names)[0].reason.startswith("shared a game file: default.xex")
    assert zip_member_names("broken.zip", b"not a zip") == []


def test_piracy_links():
    checker = LinkChecker(extra_piracy={"warez.example"})
    assert checker.check(["https://vimm.net/vault/1234"])[0].category == "piracy"
    assert checker.check(["https://warez.example/x"])[0].reason == "link to a piracy site (warez.example)"
    hosted = check_urls(["https://mega.example/file/abc/Gears%20of%20War.iso"])
    assert hosted[0].reason == "link to a game file: Gears of War.iso (disc image)"
    # GitHub releases of recomp tools are trusted, even for .elf or .pkg builds.
    assert check_urls(["https://github.com/org/tool/releases/download/v1/tool.pkg"], checker.is_allowed_url) == []


def test_talking_about_game_files_is_not_piracy():
    for text in ["my default.xex crashes in the recompiler at 0x82000000",
                 "how do I dump my own Xbox 360 disc to an ISO?",
                 "the .elf loader in ps3recomp handles relocations"]:
        verdict = run(moderator(None).review(Post(text, 1, 1)))
        assert not verdict.flagged, text


def test_mixed_signals_pick_the_bigger_category():
    text = "free nitro https://dlscord-gift.com"
    verdict = run(moderator(None).review(Post(text, 1, 1, attachment_count=1, attachment_names=["game.iso"])))
    assert verdict.label == "piracy"  # 100 piracy vs 100 scam: tie goes to piracy
    assert any("dlscord" in r for r in verdict.reasons)


# --- safe links ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "https://github.com/rexglue/rexglue-sdk/wiki",
        "https://media.tenor.com/x/funny.gif", "https://some-random-host.xyz/reaction.gif",
        "https://en.wikipedia.org/wiki/Static_recompilation", "https://halo.fandom.com/wiki/Halo_3",
        "https://zelda.wiki.gg/wiki/Link", "https://wiki.example.org/page", "https://sonic.wiki",
        "https://news.example.com/article", "https://someones-blog.net/post",
    ],
)
def test_safe_and_ordinary_links_add_no_risk(url):
    checker = LinkChecker()
    assert checker.check([url]) == []
    assert check_urls([url], checker.is_allowed_url) == []


def test_gif_or_wiki_cannot_hide_a_lookalike_or_blocked_domain():
    checker = LinkChecker()
    assert "imitates discord" in checker.check(["https://dlscord-wiki.com/page"])[0].reason
    assert checker.check(["https://kegwin.com/promo.gif"])[0].weight == 100


def test_links_from_regular_members_never_flag_on_their_own():
    post = Post("check this out https://someones-blog.net/post and https://cool.xyz/thing", 1, 1,
                account_age_days=400, member_age_hours=3000)
    # Rules alone never flag ordinary links; an unusual TLD only asks the AI for a second look.
    assert not run(moderator(None).review(post)).flagged
    assert not run(moderator(FakeAI('{"label": "ok", "confidence": 0.9}')).review(post)).flagged
