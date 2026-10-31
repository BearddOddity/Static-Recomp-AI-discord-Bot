import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .runtime_settings import apply_overrides

DEFAULT_WELCOME = (
    "Welcome to {server}, {user}! 👋 Please read {rules}. Ask me anything with `/ask` or by "
    "mentioning me, and use `/help` to open a private thread if you want a person's help."
)

PROVIDERS = (
    "anthropic",
    "openrouter",
    "groq",
    "gemini",
    "openai_compatible",
    "ollama",
    "cli",
)


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_id: int | None
    ai_provider: str
    ai_timeout: float
    community_about: str
    system_prompt: str

    anthropic_api_key: str
    anthropic_model: str

    openai_compat_base_url: str
    openai_compat_api_key: str
    openai_compat_model: str

    ollama_host: str
    ollama_model: str

    ai_cli_command: str
    ask_cooldown_seconds: float
    ask_max_per_minute: int

    openrouter_app_name: str
    openrouter_site_url: str
    openrouter_free_fallback: bool

    auto_update: bool
    update_check_minutes: float
    update_branch: str
    health_github_repo: str
    health_github_token: str
    health_pr_file: str

    docs_repos: str
    docs_routes: str
    github_token: str
    docs_refresh_minutes: float
    docs_excerpts: int
    docs_ai_query: str  # "off", "fallback" or "always"
    ask_on_mention: bool
    assistant_channel_ids: frozenset[int]
    chat_context_messages: int

    mod_enabled: bool
    mod_log_channel_id: int | None
    mod_action: str  # "delete" or "flag", for scams
    mod_piracy_action: str  # "flag" or "delete"
    mod_rule_action: str  # "flag" or "delete", for #rules violations
    mod_hate_action: str  # "flag" or "delete", for hate speech
    mod_hate_automod_rule_ids: frozenset[int]
    mod_warn_limit: int
    mod_warn_expiry_days: float
    mod_severe_confidence: float
    mod_severe_scam_review: bool
    mod_dm_warnings: bool
    mod_db_path: str
    rules_channel_id: int | None
    mod_ai_scan: str  # "off", "suspicious" or "all"
    mod_ai_model: str
    mod_min_confidence: float
    mod_exempt_role_ids: frozenset[int]
    mod_blocked_domains: frozenset[str]
    mod_allowed_domains: frozenset[str]
    mod_piracy_domains: frozenset[str]
    # Named model lists /ai-mode can switch /ask between: {"fast": "model-a,model-b"}.
    ai_modes: dict[str, str] = field(default_factory=dict)
    # Optional second provider, tried when the main one fails or is out of quota.
    ai_backup_provider: str = ""  # groq, gemini, openrouter or openai_compatible
    ai_backup_api_key: str = ""
    ai_backup_model: str = ""
    ai_backup_vision_model: str = ""  # used for moderation (images) when set
    ai_backup_base_url: str = ""  # only for openai_compatible
    appeals_enabled: bool = False  # Appeals mode: warned members get an Appeal button in their DM
    help_enabled: bool = False  # Help mode: /help opens a private help thread
    help_channel_id: int | None = None  # where those threads are made (empty = where /help is used)
    help_staff_role_id: int | None = None  # pinged by "Get a human"
    welcome_channel_id: int | None = None  # empty = no welcome message (needs the Server Members Intent)
    welcome_message: str = DEFAULT_WELCOME
    welcome_ai: bool = False  # let the AI write each welcome (otherwise only welcome_message is used)


# Settings each provider cannot work without, checked at startup so a missing
# key fails immediately instead of on the first /ask.
REQUIRED_BY_PROVIDER = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    # OpenRouter can pick a free model by itself, so only the key is required.
    "openrouter": ("OPENAI_COMPAT_API_KEY",),
    "groq": ("OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL"),
    "gemini": ("OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL"),
    "openai_compatible": ("OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL"),
    "ollama": (),
    "cli": ("AI_CLI_COMMAND",),
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _choice(name: str, default: str, options: tuple[str, ...]) -> str:
    value = os.getenv(name, default).strip().lower() or default
    if value not in options:
        raise RuntimeError(f"{name} must be one of: {', '.join(options)}")
    return value


def _set(name: str) -> frozenset[str]:
    return frozenset(p.strip().lower() for p in os.getenv(name, "").split(",") if p.strip())


def _modes(name: str) -> dict[str, str]:
    """AI_MODES="fast=a,b; smart=c,d" -> {"fast": "a,b", "smart": "c,d"}."""
    modes = {}
    for part in os.getenv(name, "").split(";"):
        mode, _, models = part.partition("=")
        if mode.strip() and models.strip():
            modes[mode.strip().lower()] = models.strip()
    return modes


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    return default if not value else value not in ("0", "false", "no", "off")


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None


def _get(name: str, default: str) -> str:
    """Like os.getenv, but an empty value ("KEY=" in .env) also means the default."""
    return os.getenv(name, "").strip() or default


def load_server_profile() -> None:
    """Fill in settings from SERVER_PROFILE, a .env-style file holding one server's
    details (its docs repos, topic, keywords). Values already set in the environment
    or .env win, so .env can override any profile setting."""
    path = os.getenv("SERVER_PROFILE", "").strip()
    if not path:
        return
    if not Path(path).is_file():
        raise RuntimeError(f"SERVER_PROFILE file not found: {path}")
    for name, value in dotenv_values(path).items():
        if value is not None and not os.getenv(name, "").strip():
            os.environ[name] = value


def load_settings(require_discord: bool = True, require_ai: bool = True) -> Settings:
    """Read settings from the environment, .env and the optional server profile.
    Command-line tools that never connect to Discord, or never call the AI, can
    skip those checks."""
    load_dotenv()
    apply_overrides()  # settings saved from Discord (/settings) win over .env
    load_server_profile()

    provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"AI_PROVIDER must be one of: {', '.join(PROVIDERS)}")

    missing = [name for name in REQUIRED_BY_PROVIDER[provider] if not os.getenv(name, "").strip()]
    if missing and require_ai:
        raise RuntimeError(f"AI_PROVIDER={provider} needs: {', '.join(missing)}")

    backup = os.getenv("AI_BACKUP_PROVIDER", "").strip().lower()
    if backup:
        if backup not in ("groq", "gemini", "openrouter", "openai_compatible"):
            raise RuntimeError("AI_BACKUP_PROVIDER must be groq, gemini, openrouter or openai_compatible")
        needs = ["AI_BACKUP_API_KEY"] + (["AI_BACKUP_BASE_URL"] if backup == "openai_compatible" else [])
        if backup != "openrouter":
            needs.append("AI_BACKUP_MODEL")
        missing = [n for n in needs if not os.getenv(n, "").strip()]
        if missing and require_ai:
            raise RuntimeError(f"AI_BACKUP_PROVIDER={backup} needs: {', '.join(missing)}")

    guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    about = _get("COMMUNITY_ABOUT", "video games")

    return Settings(
        discord_token=_required("DISCORD_TOKEN") if require_discord else os.getenv("DISCORD_TOKEN", ""),
        discord_guild_id=int(guild) if guild else None,
        ai_provider=provider,
        ai_timeout=float(os.getenv("AI_TIMEOUT", "60")),
        community_about=about,
        # Empty means the built-in personality (bot/assist.py PERSONA).
        system_prompt=os.getenv("AI_SYSTEM_PROMPT", "").strip(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        openai_compat_base_url=os.getenv("OPENAI_COMPAT_BASE_URL", ""),
        openai_compat_api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
        openai_compat_model=os.getenv("OPENAI_COMPAT_MODEL", ""),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        ai_cli_command=os.getenv("AI_CLI_COMMAND", ""),
        ask_cooldown_seconds=max(0.0, float(os.getenv("ASK_COOLDOWN_SECONDS", "10") or 10)),
        ask_max_per_minute=max(0, int(os.getenv("ASK_MAX_PER_MINUTE", "20") or 20)),
        openrouter_app_name=_get("OPENROUTER_APP_NAME", "AI Discord Bot"),
        openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", ""),
        openrouter_free_fallback=_flag("OPENROUTER_FREE_FALLBACK", True),
        auto_update=_flag("AUTO_UPDATE", True),
        update_check_minutes=float(os.getenv("UPDATE_CHECK_MINUTES", "30") or 30),
        update_branch=os.getenv("UPDATE_BRANCH", "").strip(),
        health_github_repo=os.getenv("HEALTH_GITHUB_REPO", "").strip(),
        health_github_token=os.getenv("HEALTH_GITHUB_TOKEN", "").strip(),
        # A model swap is proposed in the server profile (it's in git; .env isn't).
        health_pr_file=_get("HEALTH_PR_FILE", os.getenv("SERVER_PROFILE", "").strip()),
        docs_repos=os.getenv("DOCS_REPOS", ""),
        docs_routes=os.getenv("DOCS_ROUTES", ""),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        docs_refresh_minutes=float(os.getenv("DOCS_REFRESH_MINUTES", "360")),
        docs_excerpts=int(os.getenv("DOCS_EXCERPTS", "4")),
        docs_ai_query=_choice("DOCS_AI_QUERY", "fallback", ("off", "fallback", "always")),
        ask_on_mention=_flag("ASK_ON_MENTION", True),
        assistant_channel_ids=frozenset(int(c) for c in _set("ASSISTANT_CHANNEL_IDS")),
        chat_context_messages=max(0, int(os.getenv("CHAT_CONTEXT_MESSAGES", "8") or 8)),
        mod_enabled=_flag("MOD_ENABLED", True),
        mod_log_channel_id=_optional_int("MOD_LOG_CHANNEL_ID"),
        mod_action=_choice("MOD_ACTION", "delete", ("delete", "flag")),
        mod_piracy_action=_choice("MOD_PIRACY_ACTION", "delete", ("delete", "flag")),
        mod_rule_action=_choice("MOD_RULE_ACTION", "delete", ("delete", "flag")),
        mod_hate_action=_choice("MOD_HATE_ACTION", "delete", ("delete", "flag")),
        mod_hate_automod_rule_ids=frozenset(int(r) for r in _set("MOD_HATE_AUTOMOD_RULE_IDS")),
        mod_warn_limit=max(1, int(os.getenv("MOD_WARN_LIMIT", "3"))),
        mod_warn_expiry_days=float(os.getenv("MOD_WARN_EXPIRY_DAYS", "90")),
        mod_severe_confidence=float(os.getenv("MOD_SEVERE_CONFIDENCE", "0.9")),
        mod_severe_scam_review=_flag("MOD_SEVERE_SCAM_REVIEW", False),
        mod_dm_warnings=_flag("MOD_DM_WARNINGS", True),
        mod_db_path=os.getenv("MOD_DB_PATH", "data/moderation.sqlite3"),
        rules_channel_id=_optional_int("RULES_CHANNEL_ID"),
        mod_ai_scan=_choice("MOD_AI_SCAN", "suspicious", ("off", "suspicious", "all")),
        mod_ai_model=os.getenv("MOD_AI_MODEL", "").strip(),
        mod_min_confidence=float(os.getenv("MOD_MIN_CONFIDENCE", "0.75")),
        mod_exempt_role_ids=frozenset(int(r) for r in _set("MOD_EXEMPT_ROLE_IDS")),
        mod_blocked_domains=_set("MOD_BLOCKED_DOMAINS"),
        mod_allowed_domains=_set("MOD_ALLOWED_DOMAINS"),
        mod_piracy_domains=_set("MOD_PIRACY_DOMAINS"),
        ai_modes=_modes("AI_MODES"),
        ai_backup_provider=backup,
        ai_backup_api_key=os.getenv("AI_BACKUP_API_KEY", "").strip(),
        ai_backup_model=os.getenv("AI_BACKUP_MODEL", "").strip(),
        ai_backup_vision_model=os.getenv("AI_BACKUP_VISION_MODEL", "").strip(),
        ai_backup_base_url=os.getenv("AI_BACKUP_BASE_URL", "").strip(),
        appeals_enabled=_flag("APPEALS_ENABLED", False),
        help_enabled=_flag("HELP_ENABLED", False),
        help_channel_id=_optional_int("HELP_CHANNEL_ID"),
        help_staff_role_id=_optional_int("HELP_STAFF_ROLE_ID"),
        welcome_channel_id=_optional_int("WELCOME_CHANNEL_ID"),
        welcome_message=_get("WELCOME_MESSAGE", DEFAULT_WELCOME)[:1500],
        welcome_ai=_flag("WELCOME_AI", False),
    )
