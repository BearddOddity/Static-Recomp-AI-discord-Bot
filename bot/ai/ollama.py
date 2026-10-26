import httpx

from .base import AIProvider, Message


class OllamaProvider(AIProvider):
    """A model running on a local Ollama server (https://ollama.com)."""

    name = "ollama"
    # Needs a vision model such as llava or llama3.2-vision to use the images.
    supports_images = True

    def __init__(
        self,
        host: str,
        model: str,
        timeout: float = 120,
        client: httpx.AsyncClient | None = None,
    ):
        self._host = host.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def complete(self, messages: list[Message]) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": m.role, "content": m.content, **({"images": [i.base64 for i in m.images]} if m.images else {})}
                for m in messages
            ],
            "stream": False,
        }
        response = await self._client.post(f"{self._host}/api/chat", json=body)
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    async def aclose(self) -> None:
        await self._client.aclose()
