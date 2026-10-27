import httpx

from ..config import Settings
from .base import AIError, AIProvider, Image, Message
from .anthropic_provider import AnthropicProvider
from .cli import CLIProvider
from .fallback import FallbackProvider, FreeModelFinder, Problem
from .ollama import OllamaProvider
from .openai_compat import PRESETS, OpenAICompatibleProvider

__all__ = [
    "AIError",
    "AIProvider",
    "FallbackProvider",
    "Problem",
    "Image",
    "Message",
    "build_provider",
]


def models_from(value: str) -> list[str]:
    """A setting may list several models, comma separated: the first is used, the
    rest are backups."""
    return [m.strip() for m in value.split(",") if m.strip()]


def build_backup(settings: Settings, *, needs_images: bool = False) -> AIProvider | None:
    """The optional second provider (AI_BACKUP_*), used when the main one fails."""
    choice = settings.ai_backup_provider
    if not choice:
        return None
    base_url = PRESETS.get(choice) or settings.ai_backup_base_url
    client = httpx.AsyncClient(timeout=settings.ai_timeout)
    headers = {"X-Title": settings.openrouter_app_name} if choice == "openrouter" else {}

    def make(name: str) -> AIProvider:
        provider = OpenAICompatibleProvider(
            base_url=base_url, api_key=settings.ai_backup_api_key, model=name, extra_headers=headers, client=client
        )
        provider.name = f"{choice} (backup)"
        return provider

    models = (needs_images and settings.ai_backup_vision_model) or settings.ai_backup_model
    free = choice == "openrouter" and settings.openrouter_free_fallback
    return FallbackProvider(
        make, models_from(models), setting="AI_BACKUP_MODEL", name=f"{choice} (backup)",
        finder=FreeModelFinder(client) if free else None, needs_images=needs_images,
    )


def build_provider(
    settings: Settings, model: str = "", *, setting: str = "", needs_images: bool = False
) -> AIProvider:
    """Create the provider named by AI_PROVIDER, reading its settings.

    model overrides the configured model, e.g. a vision model for moderation, and
    setting names where it came from (for problem reports). Hosted OpenAI-style
    providers get model fallback; OpenRouter also falls back to its free models.
    """
    choice = settings.ai_provider

    if choice == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=model or settings.anthropic_model,
            timeout=settings.ai_timeout,
        )

    if choice == "ollama":
        return OllamaProvider(
            host=settings.ollama_host,
            model=model or settings.ollama_model,
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
        client = httpx.AsyncClient(timeout=settings.ai_timeout)

        def make(name: str) -> AIProvider:
            provider = OpenAICompatibleProvider(
                base_url=base_url,
                api_key=settings.openai_compat_api_key,
                model=name,
                extra_headers=extra_headers,
                client=client,
            )
            provider.name = choice
            return provider

        auto_free = choice == "openrouter" and settings.openrouter_free_fallback
        primary = FallbackProvider(
            make,
            models_from(model or settings.openai_compat_model),
            setting=setting or "OPENAI_COMPAT_MODEL",
            name=choice,
            finder=FreeModelFinder(client) if auto_free else None,
            needs_images=needs_images,
        )
        primary.backup = build_backup(settings, needs_images=needs_images)
        return primary

    raise ValueError(f"Unknown AI_PROVIDER: {choice!r}")
