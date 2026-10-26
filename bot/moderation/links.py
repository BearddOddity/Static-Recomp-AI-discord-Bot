import ipaddress
import re
from importlib import resources
from urllib.parse import urlsplit

from .signals import Signal

# Full URLs, plus bare domains such as "kegwin.com" that scams often use to dodge
# link filters. Bare domains only match common TLDs so "bot.main" isn't a link.
_URL = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_BARE_TLDS = (
    "com|net|org|io|gg|co|me|xyz|top|click|gift|icu|site|online|live|shop|fun|vip|bet|"
    "casino|win|pro|app|link|ru|cc|tk|ml|ga|cf|gq|sbs|cfd|lol|store|club|info|biz|us|tv"
)
_BARE = re.compile(
    rf"(?<![\w@./-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_BARE_TLDS}))(?:/[^\s<>()\"']*)?(?![\w-])",
    re.IGNORECASE,
)

SHORTENERS = frozenset(
    "bit.ly tinyurl.com t.co goo.gl is.gd cutt.ly rb.gy shorturl.at ow.ly s.id t.ly "
    "tiny.cc rebrand.ly bl.ink linktr.ee".split()
)
SUSPICIOUS_TLDS = frozenset(
    "xyz top click gift icu site online live shop fun vip bet casino win sbs cfd lol "
    "tk ml ga cf gq rest monster cyou buzz".split()
)
# Brands scammers imitate in lookalike domains (dlscord, steamcommunlty, ...).
# Short names only count as an exact word ("steam-gift.ru"); fuzzy matching them
# would flag ordinary words such as "stream" or "team".
EXACT_BRANDS = ("steam", "nitro", "twitch")
FUZZY_BRANDS = ("discord", "steamcommunity", "steampowered", "github", "epicgames", "youtube")


def _load_list(name: str) -> frozenset[str]:
    text = resources.files(__package__).joinpath("data", name).read_text(encoding="utf-8")
    return frozenset(
        line.strip().lower() for line in text.splitlines() if line.strip() and not line.startswith("#")
    )


DEFAULT_BLOCKED = _load_list("blocked_domains.txt")
DEFAULT_ALLOWED = _load_list("allowed_domains.txt")


def extract_urls(text: str) -> list[str]:
    urls = _URL.findall(text)
    without_full = _URL.sub(" ", text)
    urls += [m.group(0) for m in _BARE.finditer(without_full)]
    return list(dict.fromkeys(u.rstrip(".,!?:;") for u in urls))


def domain_of(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _matches(domain: str, listed: frozenset[str]) -> bool:
    """True if domain is listed or is a subdomain of a listed domain."""
    parts = domain.split(".")
    return any(".".join(parts[i:]) in listed for i in range(len(parts) - 1))


def _distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 3
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


class LinkChecker:
    def __init__(self, extra_blocked: set[str] = frozenset(), extra_allowed: set[str] = frozenset()):
        self.blocked = DEFAULT_BLOCKED | {d.lower() for d in extra_blocked}
        self.allowed = DEFAULT_ALLOWED | {d.lower() for d in extra_allowed}

    def is_allowed(self, domain: str) -> bool:
        return _matches(domain, self.allowed)

    def is_allowed_url(self, url: str) -> bool:
        return self.is_allowed(domain_of(url))

    def check(self, urls: list[str]) -> list[Signal]:
        signals = []
        for url in urls:
            domain = domain_of(url)
            if not domain:
                continue
            signals.extend(self._check_domain(domain, url))
        return signals

    def _check_domain(self, domain: str, url: str) -> list[Signal]:
        if _matches(domain, self.blocked):
            return [Signal(f"known scam domain {domain}", 100)]

        lowered = url.lower()
        if domain in ("discord.gg", "discord.com", "discordapp.com") and (
            "discord.gg/" in lowered or "/invite/" in lowered
        ):
            return [Signal(f"Discord server invite ({domain})", 10)]

        if self.is_allowed(domain):
            return []

        signals = []
        try:
            ipaddress.ip_address(domain)
            signals.append(Signal(f"link to a raw IP address {domain}", 30))
        except ValueError:
            pass
        if "xn--" in domain:
            signals.append(Signal(f"punycode domain that can imitate another {domain}", 30))
        if domain in SHORTENERS:
            signals.append(Signal(f"link shortener hides the destination ({domain})", 20))
        if domain.rsplit(".", 1)[-1] in SUSPICIOUS_TLDS:
            signals.append(Signal(f"domain uses a TLD common in scams ({domain})", 15))

        lookalike = self._lookalike(domain)
        if lookalike:
            signals.append(Signal(f"{domain} imitates {lookalike}", 60))
        return signals

    @staticmethod
    def _lookalike(domain: str) -> str | None:
        # Compare every word in the domain name (not the TLD) with each brand.
        words = re.split(r"[.-]", domain.rsplit(".", 1)[0])
        for word in words:
            for brand in EXACT_BRANDS + FUZZY_BRANDS:
                if word == brand:
                    return brand  # brand name on a domain that isn't the brand's
            for brand in FUZZY_BRANDS:
                limit = 1 if len(brand) <= 7 else 2
                if 0 < _distance(word, brand) <= limit:
                    return brand
        return None
