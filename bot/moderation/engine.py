import hashlib
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field

from ..ai import Image
from .classifier import AIVerdict, Classifier
from .files import check_attachments, check_urls
from .links import LinkChecker, extract_urls
from .signals import Signal, leading_category, total
from .text import check_text

# Score at or above which rules alone decide "scam" (e.g. a blocklisted domain).
HARD_SCORE = 80
# Score at or above which a post is flagged when the AI is off or failed.
SOFT_SCORE = 50
# Score at or above which the AI is asked to double-check in "suspicious" mode.
ASK_AI_SCORE = 20

DUPLICATE_WINDOW_SECONDS = 120
DUPLICATE_CHANNELS = 3
CACHE_SIZE = 1000


@dataclass
class Post:
    """What the engine needs from a Discord message, so it can be tested without Discord."""

    text: str
    author_id: int
    channel_id: int
    images: list[Image] = field(default_factory=list)
    attachment_count: int = 0
    # File names of all attachments, plus "archive.zip/inner/file" for zip contents.
    attachment_names: list[str] = field(default_factory=list)
    account_age_days: float | None = None
    member_age_hours: float | None = None
    rules: str = ""  # the server's #rules text, if known
    created_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class Verdict:
    label: str  # "ok", "scam", "piracy" or "rule_violation"
    confidence: float
    reasons: tuple[str, ...]
    source: str  # "rules", "ai" or "cache"
    score: int = 0
    urls: tuple[str, ...] = ()

    @property
    def flagged(self) -> bool:
        return self.label != "ok"


OK = Verdict("ok", 0.0, (), "rules")


class Moderator:
    def __init__(
        self,
        links: LinkChecker,
        classifier: Classifier | None,
        ai_scan: str = "suspicious",
        min_confidence: float = 0.75,
    ):
        self.links = links
        self.classifier = classifier if ai_scan != "off" else None
        self.ai_scan = ai_scan
        self.min_confidence = min_confidence
        self._recent: dict[int, deque] = defaultdict(deque)
        # AI verdicts by content hash, so cross-posted copies cost one AI call.
        self._cache: OrderedDict[str, AIVerdict] = OrderedDict()

    async def review(self, post: Post) -> Verdict:
        urls = extract_urls(post.text)
        signals = (
            self.links.check(urls)
            + check_urls(urls, self.links.is_allowed_url)
            + check_attachments(post.attachment_names)
            + check_text(post.text)
        )

        has_media = bool(urls or post.attachment_count)
        if has_media:
            signals += self._account_signals(post)
        duplicate = self._duplicate_signal(post)
        if duplicate:
            signals.append(duplicate)

        score = total(signals)
        rule_reasons = tuple(s.reason for s in signals)
        rule_label = leading_category(signals)

        if score >= HARD_SCORE:
            return Verdict(rule_label, min(1.0, score / 100), rule_reasons, "rules", score, tuple(urls))

        if not self.classifier or not self._should_ask_ai(post, urls, score):
            if score >= SOFT_SCORE and not self.classifier:
                return Verdict(rule_label, score / 100, rule_reasons, "rules", score, tuple(urls))
            return OK

        # Rules are part of the key: a verdict made under old rules may no longer hold.
        key = self._content_key(post) + hashlib.sha256(post.rules.encode()).hexdigest()[:16]
        source = "cache"
        ai = self._cache.get(key)
        if ai is None:
            source = "ai"
            ai = await self.classifier.classify(post.text, post.images, urls, signals, post.rules)
            if ai is not None:
                self._remember(key, ai)

        if ai is None:
            # AI failed: fall back to the rules on their own.
            if score >= SOFT_SCORE:
                return Verdict(rule_label, score / 100, rule_reasons, "rules", score, tuple(urls))
            return OK
        if ai.label != "ok" and ai.confidence >= self.min_confidence:
            return Verdict(ai.label, ai.confidence, ai.reasons + rule_reasons, source, score, tuple(urls))
        return OK

    def _should_ask_ai(self, post: Post, urls: list[str], score: int) -> bool:
        if self.ai_scan == "all":
            return bool(post.text.strip() or post.images)
        if score >= ASK_AI_SCORE:
            return True
        if post.images:
            # Scam screenshot sets usually come as several images, often from new
            # or hacked accounts. A single screenshot from a regular is skipped.
            return len(post.images) >= 2 or score > 0 or self._is_new(post)
        unknown_links = [u for u in urls if not self.links.is_allowed_url(u)]
        return bool(unknown_links) and (score > 0 or self._is_new(post))

    @staticmethod
    def _is_new(post: Post) -> bool:
        return (post.account_age_days is not None and post.account_age_days < 7) or (
            post.member_age_hours is not None and post.member_age_hours < 24
        )

    @staticmethod
    def _account_signals(post: Post) -> list[Signal]:
        signals = []
        if post.account_age_days is not None and post.account_age_days < 7:
            signals.append(Signal(f"Discord account is {post.account_age_days:.0f} days old", 15))
        if post.member_age_hours is not None and post.member_age_hours < 24:
            signals.append(Signal(f"joined the server {post.member_age_hours:.0f} hours ago", 10))
        return signals

    def _duplicate_signal(self, post: Post) -> Signal | None:
        """Hacked accounts often post the same thing in many channels within a minute."""
        fingerprint = self._content_key(post)
        history = self._recent[post.author_id]
        while history and post.created_at - history[0][0] > DUPLICATE_WINDOW_SECONDS:
            history.popleft()
        history.append((post.created_at, fingerprint, post.channel_id))
        channels = {channel for _, fp, channel in history if fp == fingerprint}
        if len(channels) >= DUPLICATE_CHANNELS:
            return Signal(f"same message posted in {len(channels)} channels within 2 minutes", 50)
        return None

    @staticmethod
    def _content_key(post: Post) -> str:
        digest = hashlib.sha256(" ".join(post.text.lower().split()).encode())
        for image in post.images:
            digest.update(hashlib.sha256(image.data).digest())
        digest.update(str(post.attachment_count).encode())
        digest.update("|".join(post.attachment_names).encode())
        return digest.hexdigest()

    def _remember(self, key: str, verdict: AIVerdict) -> None:
        self._cache[key] = verdict
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
