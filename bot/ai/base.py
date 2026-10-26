from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    role: str  # "system", "user" or "assistant"
    content: str


class AIProvider(ABC):
    """A chat-style text model. Every backend implements this one method."""

    name: str = "base"

    @abstractmethod
    async def complete(self, messages: list[Message]) -> str:
        """Return the model's reply text for the conversation so far."""

    async def aclose(self) -> None:
        """Release any HTTP clients or subprocesses. Override when needed."""
