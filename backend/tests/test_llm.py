from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from lifeos.config import Settings
from lifeos.modules.llm import build_provider
from lifeos.modules.llm.base import (
    LLMBlockedError,
    LLMDisabledError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMRequest,
    LLMUnavailableError,
)
from lifeos.modules.llm.fake import DisabledProvider, FakeProvider
from lifeos.modules.llm.gemini import GeminiProvider


class Extraction(BaseModel):
    tasks: list[str]


def _request(text: str = "Bonjour") -> LLMRequest:
    return LLMRequest(purpose="test", messages=[LLMMessage(role="user", text=text)])


async def test_fake_provider_scripted_and_structured() -> None:
    fake = FakeProvider()
    fake.push('{"tasks": ["Appeler le garage"]}', "not json")
    parsed, response = await fake.generate_structured(_request(), Extraction)
    assert parsed.tasks == ["Appeler le garage"]
    assert response.usage.input_tokens > 0
    with pytest.raises(LLMInvalidOutputError):
        await fake.generate_structured(_request(), Extraction)
    assert (await fake.generate(_request("Salut"))).text == "[fake] Salut"
    assert len(fake.requests) == 3


async def test_fake_provider_default_structured_answer() -> None:
    class Proposals(BaseModel):
        items: list[str] = []

    parsed, _ = await FakeProvider().generate_structured(_request(), Proposals)
    assert parsed.items == []
    with pytest.raises(LLMInvalidOutputError):
        await FakeProvider().generate_structured(_request(), Extraction)  # required field


async def test_disabled_provider_fails_cleanly() -> None:
    with pytest.raises(LLMDisabledError):
        await DisabledProvider().generate(_request())


def test_gemini_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="GEMINI_MODEL"):
        Settings(llm_provider="gemini")
    with pytest.raises(ValueError, match="API_KEY"):
        Settings(llm_provider="gemini", gemini_model="gemini-test")
    with pytest.raises(ValueError, match="VERTEX"):
        Settings(llm_provider="gemini", gemini_model="m", gemini_backend="vertex")


def test_provider_factory() -> None:
    assert isinstance(build_provider(Settings(llm_provider="disabled")), DisabledProvider)
    assert isinstance(build_provider(Settings(llm_provider="fake")), FakeProvider)
    gemini = build_provider(
        Settings(llm_provider="gemini", gemini_model="m", gemini_api_key=SecretStr("k"))
    )
    assert isinstance(gemini, GeminiProvider)


class _StubModels:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _gemini_with(result: Any) -> tuple[GeminiProvider, _StubModels]:
    provider = GeminiProvider(
        Settings(llm_provider="gemini", gemini_model="gemini-test", gemini_api_key=SecretStr("k"))
    )
    stub = _StubModels(result)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=stub))  # type: ignore[assignment]
    return provider, stub


def _response(text: str, finish: str = "STOP", block: str | None = None) -> Any:
    return SimpleNamespace(
        text=text,
        model_version="gemini-test-001",
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=12, candidates_token_count=5, thoughts_token_count=3
        ),
    )


async def test_gemini_maps_request_and_usage() -> None:
    provider, stub = _gemini_with(_response('{"tasks": ["a"]}'))
    request = LLMRequest(
        purpose="test",
        system="Tu es LifeOS.",
        messages=[
            LLMMessage(role="user", text="q1"),
            LLMMessage(role="assistant", text="r1"),
            LLMMessage(role="user", text="q2"),
        ],
        temperature=0.2,
    )
    parsed, response = await provider.generate_structured(request, Extraction)
    assert parsed.tasks == ["a"]
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8
    call = stub.calls[0]
    assert call["model"] == "gemini-test"
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    config = call["config"]
    assert config.system_instruction == "Tu es LifeOS."
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["properties"]["tasks"]["type"] == "array"


async def test_gemini_blocked_and_unavailable() -> None:
    from google.genai import errors

    provider, _ = _gemini_with(_response("", finish="SAFETY"))
    with pytest.raises(LLMBlockedError):
        await provider.generate(_request())

    provider, _ = _gemini_with(_response("", block="OTHER"))
    with pytest.raises(LLMBlockedError):
        await provider.generate(_request())

    provider, _ = _gemini_with(errors.ServerError(503, {"error": {"message": "overloaded"}}))
    with pytest.raises(LLMUnavailableError):
        await provider.generate(_request())

    provider, _ = _gemini_with(errors.ClientError(429, {"error": {"message": "quota"}}))
    with pytest.raises(LLMUnavailableError):
        await provider.generate(_request())
