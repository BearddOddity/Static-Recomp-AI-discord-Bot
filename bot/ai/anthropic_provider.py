import httpx

from .base import AIProvider, Message

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(AIProvider):
    """Claude via the Anthropic Messages API."""

    name = "anthropic"
    supports_images = True

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 1024,
        timeout: float = 60,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def complete(self, messages: list[Message]) -> str:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": _content(m)} for m in messages if m.role != "system"]

        body: dict = {"model": self._model, "max_tokens": self._max_tokens, "messages": turns}
        if system:
            body["system"] = system

        response = await self._client.post(
            API_URL,
            json=body,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    async def aclose(self) -> None:
        await self._client.aclose()


def _content(message: Message) -> str | list[dict]:
    if not message.images:
        return message.content
    blocks: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": image.media_type, "data": image.base64},
        }
        for image in message.images
    ]
    blocks.append({"type": "text", "text": message.content})
    return blocks
