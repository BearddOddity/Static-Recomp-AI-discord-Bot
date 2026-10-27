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


def test_restricted_free_model_is_a_model_problem_not_a_key_problem():
    from bot.ai.base import classify

    assert classify(403, "x:free is only available on agentic harnesses") == "model"
    assert classify(403, "Invalid API key") == "key"


def test_backup_provider_answers_when_models_fail_and_when_key_fails():
    from bot.ai import AIError, FallbackProvider

    class Stub:
        name = "stub"
        supports_images = True

        def __init__(self, error=None, reply="ok"):
            self.error, self.reply = error, reply

        async def complete(self, messages):
            if self.error:
                raise self.error
            return self.reply

        async def aclose(self):
            pass

    def chain(error, backup):
        provider = FallbackProvider(lambda m: Stub(error), ["a"], setting="X", name="main")
        provider.backup = backup
        return provider

    ask = [Message("user", "hi")]
    assert run(chain(AIError("rate_limit", "busy"), Stub(reply="backup")).complete(ask)) == "backup"
    assert run(chain(AIError("key", "bad key"), Stub(reply="backup")).complete(ask)) == "backup"
    with pytest.raises(AIError):  # no backup configured: the error still surfaces
        run(chain(AIError("key", "bad key"), None).complete(ask))
    with pytest.raises(AIError):  # backup failing too: the main error surfaces
        run(chain(AIError("rate_limit", "busy"), Stub(AIError("unavailable", "down"))).complete(ask))


def test_backup_settings_are_validated(monkeypatch):
    from bot import config

    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "k")
    monkeypatch.setenv("AI_BACKUP_PROVIDER", "groq")
    with pytest.raises(RuntimeError, match="AI_BACKUP_API_KEY"):
        config.load_settings()
    monkeypatch.setenv("AI_BACKUP_API_KEY", "gk")
    monkeypatch.setenv("AI_BACKUP_MODEL", "llama")
    assert config.load_settings().ai_backup_provider == "groq"
