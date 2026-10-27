"""Keeps the bot answering when a model disappears or is busy.

Free models on OpenRouter come and go, and each has tight rate limits. The
FallbackProvider tries the configured models in order, skips any that no longer
exist, and on OpenRouter falls back to whatever free models are listed today.
When the configured model is gone or the key stops working, it reports a Problem
so a person can make the fix permanent (see bot/health.py).
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .base import AIError, AIProvider, Message

log = logging.getLogger("bot.ai")

MODELS_URL = "https://openrouter.ai/api/v1/models"
FREE_LIST_SECONDS = 3600
# How many free models to try in one request before giving up.
MAX_FREE_TRIES = 6


@dataclass(frozen=True)
class FreeModel:
    id: str
    images: bool  # accepts image input
    context: int


@dataclass(frozen=True)
class Problem:
    kind: str  # "key", "model" (configured model gone) or "no_model" (nothing works)
    setting: str  # the .env setting to change, e.g. OPENAI_COMPAT_MODEL
    model: str  # the model that failed
    detail: str
    replacement: str = ""  # a model that works now, for "model"


def _is_free(pricing: dict) -> bool:
    try:
        return float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def parse_free_models(data: dict) -> list[FreeModel]:
    """Free text-output models from OpenRouter's model list, biggest context first."""
    models = []
    for item in data.get("data", []):
        architecture = item.get("architecture") or {}
        inputs = architecture.get("input_modalities") or []
        outputs = architecture.get("output_modalities") or ["text"]
        modality = str(architecture.get("modality", ""))
        if not item.get("id") or not _is_free(item.get("pricing") or {}) or "text" not in outputs:
            continue
        images = "image" in inputs or "image" in modality.split("->")[0]
        models.append(FreeModel(item["id"], images, int(item.get("context_length") or 0)))
    # ":free" variants are OpenRouter's official free tier; prefer them, then big context.
    models.sort(key=lambda m: (not m.id.endswith(":free"), -m.context))
    return models


class FreeModelFinder:
    """Reads OpenRouter's public model list (no key needed), cached for an hour."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._models: list[FreeModel] = []
        self._fetched = 0.0

    async def models(self, needs_images: bool = False) -> list[str]:
        if not self._models or time.monotonic() - self._fetched > FREE_LIST_SECONDS:
            try:
                response = await self._client.get(MODELS_URL)
                response.raise_for_status()
                self._models = parse_free_models(response.json())
                self._fetched = time.monotonic()
            except (httpx.HTTPError, ValueError):
                log.warning("Couldn't read OpenRouter's model list", exc_info=True)
        if needs_images:
            # Image-capable models first, so screenshot scams can still be read.
            return [m.id for m in self._models if m.images] + [m.id for m in self._models if not m.images]
        return [m.id for m in self._models]


class FallbackProvider(AIProvider):
    """Tries models in order: the one that worked last, the configured ones, then
    (with a finder) today's free OpenRouter models."""

    def __init__(
        self,
        make: Callable[[str], AIProvider],
        models: list[str],
        *,
        setting: str,
        name: str,
        finder: FreeModelFinder | None = None,
        needs_images: bool = False,
        supports_images: bool = True,
    ):
        self._make = make
        self._providers: dict[str, AIProvider] = {}
        self.configured = [m for m in models if m]
        self.setting = setting
        self.name = name
        self.supports_images = supports_images
        self._finder = finder
        self._needs_images = needs_images
        self.current = self.configured[0] if self.configured else ""
        self.dead: set[str] = set()  # models that don't exist (any more)
        self.on_problem: Callable[[Problem], Awaitable[None]] | None = None
        self.backup: AIProvider | None = None  # another provider, tried when this one fails

    def set_models(self, models: list[str]) -> None:
        """Switch to another model list (the first is used, the rest are backups)."""
        self.configured = [m for m in models if m]
        self.current = self.configured[0] if self.configured else ""
        self.dead.clear()

    def _provider(self, model: str) -> AIProvider:
        if model not in self._providers:
            self._providers[model] = self._make(model)
        return self._providers[model]

    async def _candidates(self):
        seen = set()
        for model in [self.current, *self.configured]:
            if model and model not in seen and model not in self.dead:
                seen.add(model)
                yield model
        if self._finder is None:
            return
        tries = 0
        for model in await self._finder.models(self._needs_images):
            if model in seen or model in self.dead:
                continue
            seen.add(model)
            tries += 1
            if tries > MAX_FREE_TRIES:
                return
            yield model

    async def complete(self, messages: list[Message]) -> str:
        last: AIError | None = None
        async for model in self._candidates():
            try:
                reply = await self._provider(model).complete(messages)
            except AIError as error:
                last = error
                log.warning("Model %s failed (%s): %s", model, error.kind, error)
                if error.kind == "key":
                    await self._report(Problem("key", self.setting, model, str(error)))
                    if (reply := await self._from_backup(messages)) is not None:
                        return reply
                    raise
                if error.kind == "model":
                    self.dead.add(model)
                continue  # busy or gone: try the next one
            if model != self.current:
                log.warning("Switched %s from %s to %s", self.setting, self.current or "(auto)", model)
                self.current = model
            primary = self.configured[0] if self.configured else ""
            if primary in self.dead:
                await self._report(Problem("model", self.setting, primary, self._why(primary), replacement=model))
            return reply
        if last is None or last.kind == "model":
            # Not a passing rate limit: nothing on the list works at all.
            primary = self.configured[0] if self.configured else "(automatic)"
            await self._report(Problem("no_model", self.setting, primary, str(last) if last else "no models to try"))
        if (reply := await self._from_backup(messages)) is not None:
            return reply
        raise last or AIError("model", f"No model is available for {self.setting}")

    async def _from_backup(self, messages: list[Message]) -> str | None:
        """The backup provider's reply, or None when there is none or it failed too."""
        if self.backup is None:
            return None
        try:
            reply = await self.backup.complete(messages)
        except AIError as error:
            log.warning("Backup provider failed too (%s): %s", error.kind, error)
            return None
        log.warning("Answered %s from the backup provider %s", self.setting, self.backup.name)
        return reply

    def _why(self, model: str) -> str:
        return f"{model} is no longer available from {self.name}."

    async def _report(self, problem: Problem) -> None:
        if self.on_problem is not None:
            try:
                await self.on_problem(problem)
            except Exception:
                log.exception("Reporting an AI problem failed")

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
        if self.backup is not None:
            await self.backup.aclose()
