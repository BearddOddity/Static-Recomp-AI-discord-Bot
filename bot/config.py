import os
from dataclasses import dataclass

from dotenv import load_dotenv

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

    openrouter_app_name: str
    openrouter_site_url: str

    docs_repos: str
    docs_routes: str
    github_token: str
    docs_refresh_minutes: float
    docs_excerpts: int

    mod_enabled: bool
    mod_log_channel_id: int | None
    mod_action: str  # "flag" or "delete", for scams
    mod_piracy_action: str  # "flag" or "delete"
    mod_timeout_minutes: int
    mod_ai_scan: str  # "off", "suspicious" or "all"
    mod_ai_model: str
    mod_min_confidence: float
    mod_exempt_role_ids: frozenset[int]
    mod_blocked_domains: frozenset[str]
    mod_allowed_domains: frozenset[str]


# Settings each provider cannot work without, checked at startup so a missing
# key fails immediately instead of on the first /ask.
REQUIRED_BY_PROVIDER = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openrouter": ("OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL"),
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


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None


def load_settings(require_discord: bool = True, require_ai: bool = True) -> Settings:
    """Read settings from the environment and .env. Command-line tools that never
    connect to Discord, or never call the AI, can skip those checks."""
    load_dotenv()

    provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"AI_PROVIDER must be one of: {', '.join(PROVIDERS)}")

    missing = [name for name in REQUIRED_BY_PROVIDER[provider] if not os.getenv(name, "").strip()]
    if missing and require_ai:
        raise RuntimeError(f"AI_PROVIDER={provider} needs: {', '.join(missing)}")

    guild = os.getenv("DISCORD_GUILD_ID", "").strip()

    return Settings(
        discord_token=_required("DISCORD_TOKEN") if require_discord else os.getenv("DISCORD_TOKEN", ""),
        discord_guild_id=int(guild) if guild else None,
        ai_provider=provider,
        ai_timeout=float(os.getenv("AI_TIMEOUT", "60")),
        system_prompt=os.getenv(
            "AI_SYSTEM_PROMPT",
            "You are a helpful assistant for a Discord community about static recompiled games.",
        ),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        openai_compat_base_url=os.getenv("OPENAI_COMPAT_BASE_URL", ""),
        openai_compat_api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
        openai_compat_model=os.getenv("OPENAI_COMPAT_MODEL", ""),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        ai_cli_command=os.getenv("AI_CLI_COMMAND", ""),
        ask_cooldown_seconds=float(os.getenv("ASK_COOLDOWN_SECONDS", "30")),
        openrouter_app_name=os.getenv("OPENROUTER_APP_NAME", "Static Recomp AI Discord Bot"),
        openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", ""),
        docs_repos=os.getenv("DOCS_REPOS", ""),
        docs_routes=os.getenv("DOCS_ROUTES", ""),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        docs_refresh_minutes=float(os.getenv("DOCS_REFRESH_MINUTES", "360")),
        docs_excerpts=int(os.getenv("DOCS_EXCERPTS", "4")),
        mod_enabled=os.getenv("MOD_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off"),
        mod_log_channel_id=_optional_int("MOD_LOG_CHANNEL_ID"),
        mod_action=_choice("MOD_ACTION", "flag", ("flag", "delete")),
        mod_piracy_action=_choice("MOD_PIRACY_ACTION", "flag", ("flag", "delete")),
        mod_timeout_minutes=int(os.getenv("MOD_TIMEOUT_MINUTES", "0") or 0),
        mod_ai_scan=_choice("MOD_AI_SCAN", "suspicious", ("off", "suspicious", "all")),
        mod_ai_model=os.getenv("MOD_AI_MODEL", "").strip(),
        mod_min_confidence=float(os.getenv("MOD_MIN_CONFIDENCE", "0.75")),
        mod_exempt_role_ids=frozenset(int(r) for r in _set("MOD_EXEMPT_ROLE_IDS")),
        mod_blocked_domains=_set("MOD_BLOCKED_DOMAINS"),
        mod_allowed_domains=_set("MOD_ALLOWED_DOMAINS"),
    )
