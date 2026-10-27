"""Settings that people with the right permissions can change from Discord (/settings).

Changes are saved in data/settings.json (next to the moderation database) and win over
.env and the server profile on the next start. Only the settings listed in FIELDS can be
changed this way: DISCORD_TOKEN and AI_CLI_COMMAND (which runs a command) never are.

If saved settings stop the bot from starting, they are set aside as settings.rejected.json
and the bot starts from .env instead, so a typo in Discord can't lock you out.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

GROUPS = ("Channels", "AI", "Keys", "Moderation", "Assistant", "Community")
GROUP_HELP = {
    "Channels": "Where the bot reports and which channels it reads",
    "AI": "Which AI provider and models answer and moderate",
    "Keys": "API keys and tokens (administrators only; never shown again)",
    "Moderation": "Scam, piracy and rule enforcement",
    "Assistant": "How the bot answers members and updates itself",
    "Community": "Welcome message, help threads and appeals",
}


@dataclass(frozen=True)
class Field:
    key: str  # the .env name
    label: str
    group: str
    kind: str  # text, paragraph, secret, url, channel, id, ids, int, float, bool, choice
    help: str = ""
    admin: bool = False  # needs Administrator (or the server owner), not just Manage Server
    choices: tuple[str, ...] = ()
    low: float = 0
    high: float = 0
    live: bool = False  # applied at once and stored by /tune, no restart
    max_length: int = 300

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


def _f(*args, **kwargs) -> Field:
    return Field(*args, **kwargs)


_FIELDS = [
    # Channels
    _f("MOD_LOG_CHANNEL_ID", "Mod log channel", "Channels", "channel", "Where case reports and alerts are posted"),
    _f("RULES_CHANNEL_ID", "Rules channel", "Channels", "channel", "The channel the bot reads the rules from (empty = find it)"),
    _f("ASSISTANT_CHANNEL_IDS", "Assistant channels", "Channels", "ids", "Channels where the bot answers every message (IDs, comma separated)"),
    _f("DISCORD_GUILD_ID", "Server ID", "Channels", "id", "Makes slash commands appear at once (restart needed)"),
    # AI
    _f("AI_PROVIDER", "AI provider", "AI", "choice", "The main AI service", admin=True,
       choices=("openrouter", "groq", "gemini", "anthropic", "openai_compatible", "ollama")),
    _f("OPENAI_COMPAT_MODEL", "Models for /ask", "AI", "text", "First is used, the rest are backups (comma separated)", max_length=800),
    _f("MOD_AI_MODEL", "Models for moderation", "AI", "text", "Should accept images (comma separated)", max_length=800),
    _f("OPENROUTER_FREE_FALLBACK", "Free-model fallback", "AI", "bool", "OpenRouter only: try today's free models when yours fail"),
    _f("AI_BACKUP_PROVIDER", "Backup provider", "AI", "choice", "Used when the main AI fails or runs out", admin=True,
       choices=("", "groq", "gemini", "openrouter", "openai_compatible")),
    _f("AI_BACKUP_MODEL", "Backup models", "AI", "text", "Models on the backup provider", max_length=800),
    _f("AI_BACKUP_VISION_MODEL", "Backup vision models", "AI", "text", "Backup models for moderation (images)", max_length=800),
    _f("OPENAI_COMPAT_BASE_URL", "Custom API address", "AI", "url", "Only for the openai_compatible provider", admin=True),
    _f("AI_MODES", "AI modes", "AI", "paragraph", "For /ai-mode: fast=a,b; smart=c,d", max_length=1500),
    # Keys
    _f("OPENAI_COMPAT_API_KEY", "OpenRouter / Groq / Gemini key", "Keys", "secret", "The main AI key", admin=True),
    _f("ANTHROPIC_API_KEY", "Anthropic key", "Keys", "secret", "Only for the anthropic provider", admin=True),
    _f("AI_BACKUP_API_KEY", "Backup AI key", "Keys", "secret", "Key for the backup provider", admin=True),
    _f("GITHUB_TOKEN", "GitHub token (docs)", "Keys", "secret", "Read-only; raises the docs rate limit", admin=True),
    _f("HEALTH_GITHUB_TOKEN", "GitHub token (health PRs)", "Keys", "secret", "Lets the bot open pull requests and issues", admin=True),
    _f("HEALTH_GITHUB_REPO", "Health repository", "Keys", "text", "owner/repo the bot opens health PRs in", admin=True),
    # Moderation
    _f("MOD_ENABLED", "Moderation", "Moderation", "bool", "Turn scam, piracy and rule checks on or off"),
    _f("MOD_AI_SCAN", "AI scan mode", "Moderation", "choice", "Which messages the AI reviews", choices=("off", "suspicious", "all")),
    _f("MOD_MIN_CONFIDENCE", "Confidence needed", "Moderation", "float", "0.50-0.99: higher = fewer false alarms", low=0.5, high=0.99, live=True),
    _f("MOD_ACTION", "Scam action", "Moderation", "choice", "delete the message or just flag it", choices=("delete", "flag")),
    _f("MOD_PIRACY_ACTION", "Piracy action", "Moderation", "choice", "For piracy the AI suspects", choices=("delete", "flag")),
    _f("MOD_RULE_ACTION", "Rule-break action", "Moderation", "choice", "For breaking a rule from the rules channel", choices=("delete", "flag")),
    _f("MOD_HATE_ACTION", "Hate speech action", "Moderation", "choice", "A timeout always happens; this is about the message", choices=("delete", "flag")),
    _f("MOD_WARN_LIMIT", "Warnings before timeout", "Moderation", "int", "1-20", low=1, high=20),
    _f("MOD_WARN_EXPIRY_DAYS", "Warning expiry (days)", "Moderation", "float", "0 = never expire", low=0, high=3650),
    _f("MOD_DM_WARNINGS", "DM members on warning", "Moderation", "bool", "Send the member a DM with the reason"),
    _f("APPEALS_ENABLED", "Allow appeals", "Moderation", "bool", "Warned members get an Appeal button in their DM"),
    _f("MOD_EXEMPT_ROLE_IDS", "Exempt roles", "Moderation", "ids", "Role IDs that are never moderated (comma separated)"),
    # Assistant
    _f("COMMUNITY_ABOUT", "Community topic", "Assistant", "text", "What the server is about, in a few words", max_length=400),
    _f("AI_SYSTEM_PROMPT", "Personality", "Assistant", "paragraph", "Replaces the built-in personality (empty = built-in)", admin=True, max_length=3000),
    _f("CHAT_CONTEXT_MESSAGES", "Chat context", "Assistant", "int", "Recent messages the AI reads: 0-20", low=0, high=20, live=True),
    _f("ASK_COOLDOWN_SECONDS", "Cooldown (seconds)", "Assistant", "float", "Wait between a member's questions; 0 = off", low=0, high=3600),
    _f("ASK_ON_MENTION", "Answer mentions", "Assistant", "bool", "Answer when someone @mentions the bot"),
    _f("DOCS_REPOS", "Docs repositories", "Assistant", "text", "owner/repo, owner/repo.wiki ... (the source of truth)", admin=True, max_length=800),
    _f("DOCS_ROUTES", "Docs keyword routes", "Assistant", "text", "xbox=owner/repo; ps3=owner/repo", admin=True, max_length=800),
    _f("DOCS_EXCERPTS", "Docs sections per answer", "Assistant", "int", "1-10", low=1, high=10),
    _f("DOCS_AI_QUERY", "AI docs search", "Assistant", "choice", "fallback, always (best, costs more) or off", choices=("fallback", "always", "off")),
    _f("AUTO_UPDATE", "Self-update", "Assistant", "bool", "Pull new code from GitHub and restart"),
    # Community
    _f("WELCOME_CHANNEL_ID", "Welcome channel", "Community", "channel",
       "Empty = no welcome. Needs the Server Members Intent in the Developer Portal"),
    _f("WELCOME_MESSAGE", "Welcome message", "Community", "paragraph",
       "{user} {server} {rules} are filled in (empty = the default)", max_length=1500),
    _f("HELP_ENABLED", "Help threads", "Community", "bool", "Let members open a private help thread with /help"),
    _f("HELP_CHANNEL_ID", "Help channel", "Community", "channel", "Where help threads are made (empty = where /help is used)"),
    _f("HELP_STAFF_ROLE_ID", "Help staff role", "Community", "id", "Pinged when a member asks for a human"),
]
FIELDS: dict[str, Field] = {f.key: f for f in _FIELDS}
assert len(FIELDS) == len(_FIELDS)


def settings_path() -> Path:
    return Path(os.getenv("MOD_DB_PATH") or "data/moderation.sqlite3").parent / "settings.json"


def fields_in(group: str) -> list[Field]:
    return [f for f in _FIELDS if f.group == group]


def validate(field: Field, raw: str) -> str:
    """The cleaned value to store, or ValueError saying what is wrong. An empty value
    clears a setting (not a key: for those, empty means cancel)."""
    value = raw.strip()
    kind = field.kind
    if kind == "secret":
        if not 8 <= len(value) <= 400 or re.search(r"[\s\"'#\\]", value):
            raise ValueError("A key is 8 to 400 characters with no spaces, quotes or # signs.")
        return value
    if not value:
        if kind == "choice" and "" not in field.choices:
            raise ValueError(f"Choose one of: {', '.join(field.choices)}")
        return ""
    if kind in ("text", "url", "paragraph"):
        limit = min(field.max_length, 4000)
        if len(value) > limit:
            raise ValueError(f"Too long: {len(value)} characters, the limit is {limit}.")
        if re.search(r"[\x00-\x08\x0b-\x1f]", value) or (kind != "paragraph" and "\n" in value):
            raise ValueError("That has characters this setting can't hold (line breaks or control characters).")
        if kind == "url" and not re.fullmatch(r"https://\S+|http://(localhost|127\.0\.0\.1)\S*", value):
            raise ValueError("Use an https:// address.")
        return value
    if kind in ("channel", "id"):
        if not re.fullmatch(r"\d{15,20}", value):
            raise ValueError("That isn't an ID. Turn on Developer Mode, right-click, Copy ID.")
        return value
    if kind == "ids":
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts or any(not re.fullmatch(r"\d{15,20}", p) for p in parts):
            raise ValueError("Use IDs separated by commas.")
        return ",".join(parts)
    if kind in ("int", "float"):
        try:
            number = int(value) if kind == "int" else float(value)
        except ValueError:
            raise ValueError("That isn't a number.") from None
        if not field.low <= number <= field.high:
            raise ValueError(f"Use a number from {field.low:g} to {field.high:g}.")
        return str(number)
    if kind == "bool":
        if value.lower() not in ("true", "false"):
            raise ValueError("Use on or off.")
        return value.lower()
    if kind == "choice":
        if value.lower() not in field.choices:
            raise ValueError(f"Choose one of: {', '.join(c or 'off' for c in field.choices)}")
        return value.lower()
    raise ValueError("Unknown setting type.")


def display(field: Field, value: str) -> str:
    """How a value is shown in Discord: secrets never are."""
    if field.secret:
        return "✅ set" if value else "not set"
    if not value:
        return "not set"
    if field.kind == "channel":
        return f"<#{value}>"
    if field.kind == "ids":
        return ", ".join(f"<#{v}>" for v in value.split(","))[:150]
    if field.kind == "bool":
        return "on" if value.lower() not in ("0", "false", "no", "off") else "off"
    text = value.replace("\n", " ")
    return text if len(text) <= 120 else text[:117] + "…"


# --- the saved file -------------------------------------------------------------

def load_overrides(path: Path | None = None) -> dict[str, str]:
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in FIELDS and not FIELDS[k].live and isinstance(v, str)}


def save_overrides(overrides: dict[str, str], path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(overrides, indent=1), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)  # holds keys: the owner only (a no-op on Windows)
    except OSError:
        pass
    os.replace(temp, path)


def current(key: str) -> str:
    """The saved value, else what the running bot started with."""
    saved = load_overrides()
    return saved[key] if key in saved else os.getenv(key, "")


# --- applying at startup --------------------------------------------------------

_original: dict[str, str | None] = {}
rejected_reason = ""  # set when saved settings were set aside at startup


def apply_overrides() -> None:
    """Put the saved settings into the environment, over .env. Call after load_dotenv."""
    revert_overrides()
    for key, value in load_overrides().items():
        _original[key] = os.environ.get(key)
        os.environ[key] = value


def revert_overrides() -> None:
    for key, old in _original.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
    _original.clear()


def reject_overrides(reason: str) -> bool:
    """Set the saved settings aside after they stopped the bot from starting. False if
    there were none, so the problem is in .env itself."""
    global rejected_reason
    path = settings_path()
    if not path.is_file():
        return False
    revert_overrides()
    os.replace(path, path.with_name("settings.rejected.json"))
    rejected_reason = reason
    return True
