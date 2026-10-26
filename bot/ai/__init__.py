from ..config import Settings
from .base import AIProvider, Message
from .anthropic_provider import AnthropicProvider
from .cli import CLIProvider
from .ollama import OllamaProvider
from .openai_compat import PRESETS, OpenAICompatibleProvider

__all__ = [
    "AIProvider",
    "Message",
    "build_provider",
]


def build_provider(settings: Settings) -> AIProvider:
    """Create the provider named by AI_PROVIDER, reading its settings."""
    choice = settings.ai_provider

    if choice == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout=settings.ai_timeout,
        )

    if choice == "ollama":
        return OllamaProvider(
            host=settings.ollama_host,
            model=settings.ollama_model,
            timeout=settings.ai_timeout,
        )

    if choice == "cli":
        return CLIProvider(command=settings.ai_cli_command, timeout=settings.ai_timeout)

    if choice in PRESETS or choice == "openai_compatible":
        base_url = PRESETS.get(choice) or settings.openai_compat_base_url
        extra_headers = {}
        if choice == "openrouter":
            # Optional headers OpenRouter uses to credit the app on its rankings.
            extra_headers["X-Title"] = settings.openrouter_app_name
            if settings.openrouter_site_url:
                extra_headers["HTTP-Referer"] = settings.openrouter_site_url
        provider = OpenAICompatibleProvider(
            base_url=base_url,
            api_key=settings.openai_compat_api_key,
            model=settings.openai_compat_model,
            timeout=settings.ai_timeout,
            extra_headers=extra_headers,
        )
        provider.name = choice
        return provider

    raise ValueError(f"Unknown AI_PROVIDER: {choice!r}")
