import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass


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
