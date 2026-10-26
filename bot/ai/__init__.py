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
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=settings.openai_compat_api_key,
            model=settings.openai_compat_model,
            timeout=settings.ai_timeout,
        )

    raise ValueError(f"Unknown AI_PROVIDER: {choice!r}")
