import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
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


# --- Discord actions -----------------------------------------------------------


def test_enforce_deletes_times_out_and_reports_with_evidence():
    from bot.moderation.discord_glue import enforce
    from bot.moderation.engine import Verdict

    settings = MagicMock(mod_action="delete", mod_piracy_action="flag", mod_timeout_minutes=60, mod_log_channel_id=99)
    log_channel = MagicMock(spec=discord.TextChannel)
    log_channel.send = AsyncMock()
    message = MagicMock()
    message.content = ""
    message.created_at = discord.utils.utcnow()
    message.delete = AsyncMock()
    message.author = MagicMock(spec=discord.Member)
    message.author.id = 5
    message.author.mention = "<@5>"
    message.author.timeout = AsyncMock()
    message.guild.get_channel.return_value = log_channel

    post = Post("", 5, 1, images=example_images(), attachment_count=4)
    verdict = Verdict("scam", 0.95, ("fake giveaway",), "ai")
    run(enforce(message, post, verdict, settings))

    message.delete.assert_awaited_once()
    message.author.timeout.assert_awaited_once()
    kwargs = log_channel.send.await_args.kwargs
    assert len(kwargs["files"]) == 4
    assert "deleted the message" in kwargs["embed"].fields[2].value
