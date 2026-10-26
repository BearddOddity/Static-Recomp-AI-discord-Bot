import re

from .signals import Signal

_I = re.IGNORECASE

CRYPTO = re.compile(
    r"\b(usdt|tether|btc|bitcoin|eth|ethereum|crypto(currency)?|casino|airdrop|trx|solana|"
    r"memecoin|wallet|withdraw(al)?s?)\b",
    _I,
)
GIVEAWAY = re.compile(
    r"\b(giveaway|giving away|free|bonus|reward|claim|promo\s*code|withdraw(al)?|gift(ing)?)\b", _I
)
CELEBRITY = re.compile(r"\b(mr\.?\s?beast|elon|musk|kai cenat|ishowspeed|ronaldo|trump)\b", _I)
MONEY_AMOUNT = re.compile(r"(\$\s?\d[\d,.]*\s?k?|\d[\d,.]*\s?(usdt|usd|\$))", _I)

# Each pattern is (regex, reason, weight), plus a category for the piracy ones.
TEXT_PATTERNS = [
    (re.compile(r"\b(promo|bonus|special|referral)\s*code\b|\buse (the )?code\b", _I),
     "asks people to enter a promo or bonus code", 15),
    (re.compile(r"free\s+(discord\s+)?nitro|nitro\s+(gift|giveaway|for free|drop)|discord\s+gift", _I),
     "free Nitro or Discord gift offer", 40),
    (re.compile(r"(free|\$\s?\d+)\s+steam\s+(gift|card|key|giveaway)|steam\s+(gift|giveaway)", _I),
     "free Steam gift offer", 40),
    (re.compile(r"accidental+y\s+reported|reported\s+your\s+account|your account (will be|has been) (banned|disabled)", _I),
     "fake 'your account was reported' story", 40),
    (re.compile(r"will be deleted|deleted (in|after) an hour|only the (fastest|first)|limited time|hurry|act fast|don'?t miss", _I),
     "pressure to act fast", 10),
    (re.compile(r"\b(dm|message|text|contact) me\b.{0,60}\b(earn|profit|invest|income|crypto|forex|\$)", _I | re.S),
     "asks people to DM about money", 25),
    (re.compile(r"\b(teach|help)\s+(\d+\s+)?(people|persons|members).{0,40}\b(earn|profit|make \$)", _I | re.S),
     "'I'll teach people to earn' offer", 35),
    (re.compile(r"withdrawal (success|successful)|successfully withdr", _I),
     "fake withdrawal-success claim", 25),
    (re.compile(r"@(everyone|here)\b", _I),
     "tries to ping @everyone or @here", 15),
]

# Piracy: asking for or offering game files instead of dumping your own copy.
_FILES = r"(rom|roms|iso|isos|xex|pkg|nsp|xci|wbfs|game files?)"
_SHARING = r"(download|dl link|link|mega|mediafire|torrent|gdrive|google drive|send|share)"
PIRACY_PATTERNS = [
    (re.compile(
        rf"\b{_FILES}\b.{{0,50}}\b{_SHARING}\b|\b{_SHARING}\b.{{0,50}}\b{_FILES}\b", _I | re.S),
     "mentions downloading or sharing game files", 20, "piracy"),
    (re.compile(r"\b(where|how)\b.{0,30}\b(download|get|find)\b.{0,30}\b(rom|roms|iso|isos)\b", _I | re.S),
     "asks where to download ROMs or ISOs", 20, "piracy"),
]


def check_text(text: str) -> list[Signal]:
    if not text.strip():
        return []
    signals = []
    has_crypto = bool(CRYPTO.search(text))
    if has_crypto and GIVEAWAY.search(text):
        signals.append(Signal("crypto giveaway, casino or bonus promotion", 35))
    if CELEBRITY.search(text) and (has_crypto or GIVEAWAY.search(text) or MONEY_AMOUNT.search(text)):
        signals.append(Signal("celebrity name used to promote money or crypto", 25))
    for pattern, reason, weight, *category in TEXT_PATTERNS + PIRACY_PATTERNS:
        if pattern.search(text):
            signals.append(Signal(reason, weight, *category))
    return signals
