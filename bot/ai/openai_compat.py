import httpx

from .base import AIError, AIProvider, Message, check_response

# Hosted services that speak the OpenAI chat-completions API. Free tiers exist
# on these, so users can get a model without running anything themselves.
PRESETS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


class OpenAICompatibleProvider(AIProvider):
    """Any endpoint that implements POST {base_url}/chat/completions."""

    name = "openai-compatible"
    # The request format allows images; the chosen model must also support vision.
    supports_images = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._extra_headers = extra_headers or {}
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def complete(self, messages: list[Message]) -> str:
        body = {
            "model": self._model,
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}", **self._extra_headers},
            )
        except httpx.TransportError as error:
            raise AIError("unavailable", f"{self.name}: {error!r}") from error
        data = check_response(response, f"{self.name} ({self._model})")
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()


def _content(message: Message) -> str | list[dict]:
    if not message.images:
        return message.content
    parts: list[dict] = [{"type": "text", "text": message.content}]
    parts.extend(
        {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.base64}"}}
        for i in message.images
    )
    return parts
