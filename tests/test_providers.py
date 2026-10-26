import asyncio
import json

import httpx
import pytest

from bot.ai import AnthropicProvider, Message, OllamaProvider, OpenAICompatibleProvider
from bot.ai.cli import CLIProvider

CONVERSATION = [Message("system", "Be brief."), Message("user", "Hi")]


def run(coro):
    return asyncio.run(coro)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_anthropic_moves_system_prompt_and_reads_text_blocks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "Hello"}]}
        )

    provider = AnthropicProvider("key-123", "claude-sonnet-5-5", client=mock_client(handler))
    assert run(provider.complete(CONVERSATION)) == "Hello"
    assert seen["key"] == "key-123"
    assert seen["body"]["system"] == "Be brief."
    assert seen["body"]["messages"] == [{"role": "user", "content": "Hi"}]


def test_openai_compatible_posts_to_chat_completions():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Free answer"}}]}
        )

    provider = OpenAICompatibleProvider(
        "https://example.test/v1/", "tok", "some-model", client=mock_client(handler)
    )
    assert run(provider.complete(CONVERSATION)) == "Free answer"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer tok"


def test_ollama_uses_local_chat_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "Local"}})

    provider = OllamaProvider("http://localhost:11434", "llama3.2", client=mock_client(handler))
    assert run(provider.complete(CONVERSATION)) == "Local"


def test_cli_provider_sends_prompt_on_stdin():
    provider = CLIProvider(command="cat")
    reply = run(provider.complete([Message("user", "ping")]))
    assert "ping" in reply


def test_cli_provider_rejects_empty_command():
    with pytest.raises(ValueError):
        CLIProvider(command="   ")
