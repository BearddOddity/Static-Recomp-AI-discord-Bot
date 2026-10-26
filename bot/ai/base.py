import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str  # e.g. "image/png"

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode()


@dataclass(frozen=True)
class Message:
    role: str  # "system", "user" or "assistant"
    content: str
    images: tuple[Image, ...] = ()


class AIProvider(ABC):
    """A chat-style text model. Every backend implements this one method."""

    name: str = "base"
    # Whether complete() sends Message.images to the model. Providers that
    # can't see images ignore them, so callers should check this first.
    supports_images: bool = False

    @abstractmethod
    async def complete(self, messages: list[Message]) -> str:
        """Return the model's reply text for the conversation so far."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, a no-op by default
        """Release any HTTP clients or subprocesses. Override when needed."""


class AIError(RuntimeError):
    """A provider refused or failed a request. kind says what the owner must do:

    key          the API key is wrong, revoked or out of credit: a person must replace it
    model        the model doesn't exist (any more): switch to another model
    rate_limit   too many requests for now: try another model or wait
    unavailable  the provider is down or timed out: try again later
    other        anything else
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


_MODEL_WORDS = ("model", "endpoints found", "no endpoints")


def classify(status: int | None, message: str) -> str:
    text = message.lower()
    if "flagged" in text or "moderation" in text:
        return "other"  # OpenRouter's 403 for a message its filter blocked; the key is fine
    if status in (401, 403) or "api key" in text or "auth" in text and "credential" in text:
        return "key"
    if status == 402 or "credit" in text:
        return "key"  # out of credit: only a person can top up or swap the key
    if status == 404 or (status == 400 and any(w in text for w in _MODEL_WORDS)):
        return "model"
    if status == 429:
        return "rate_limit"
    if status is None or status >= 500:
        return "unavailable"
    return "other"


def error_message(data: object) -> str:
    """The human-readable part of an error body, in the formats providers use."""
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)
    return str(data)


def check_response(response: httpx.Response, provider: str) -> dict:
    """Raise AIError for a failed request, or return the JSON body."""
    try:
        data = response.json()
    except ValueError:
        data = {"error": response.text[:300]}
    status = response.status_code
    # OpenRouter (and some others) send HTTP 200 with an error body and its own code.
    if status >= 400 or (isinstance(data, dict) and "error" in data):
        if status < 400 and isinstance(data.get("error"), dict):
            code = data["error"].get("code")
            status = code if isinstance(code, int) else status
        message = error_message(data)
        raise AIError(classify(status, message), f"{provider}: {message} (HTTP {status})", status)
    return data
