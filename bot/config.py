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


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    load_dotenv()

    provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"AI_PROVIDER must be one of: {', '.join(PROVIDERS)}")

    guild = os.getenv("DISCORD_GUILD_ID", "").strip()

    return Settings(
        discord_token=_required("DISCORD_TOKEN"),
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
    )
