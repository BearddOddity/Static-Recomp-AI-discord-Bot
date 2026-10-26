import httpx

from .base import AIProvider, Message

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
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}", **self._extra_headers},
        )
        response.raise_for_status()
        data = response.json()
        # OpenRouter (and some others) return HTTP 200 with an error body.
        if "error" in data:
            raise RuntimeError(f"{self.name} error: {data['error']}")
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    async def aclose(self) -> None:
        await self._client.aclose()
