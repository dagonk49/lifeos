"""Gemini adapter (google-genai SDK), for the Gemini Developer API or Vertex AI."""

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from lifeos.config import Settings
from lifeos.modules.llm.base import (
    LLMBlockedError,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    LLMUsage,
    parse_structured,
)

_BLOCKED_FINISH_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}


def _to_contents(messages: list[LLMMessage]) -> list[types.Content]:
    return [
        types.Content(
            role="model" if m.role == "assistant" else "user",
            parts=[types.Part.from_text(text=m.text)],
        )
        for m in messages
    ]


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        assert settings.gemini_model is not None  # enforced by Settings validation
        self.model = settings.gemini_model
        http_options = types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000))
        if settings.gemini_backend == "vertex":
            self._client = genai.Client(
                vertexai=True,
                project=settings.gemini_vertex_project,
                location=settings.gemini_vertex_location,
                http_options=http_options,
            )
        else:
            assert settings.gemini_api_key is not None
            self._client = genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),
                http_options=http_options,
            )

    def _config(
        self, request: LLMRequest, schema: type[BaseModel] | None = None
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            response_mime_type="application/json" if schema else None,
            response_json_schema=schema.model_json_schema() if schema else None,
        )

    async def _call(
        self, request: LLMRequest, schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=_to_contents(request.messages),
                config=self._config(request, schema),
            )
        except genai_errors.ServerError as exc:
            raise LLMUnavailableError(f"gemini server error {exc.code}") from exc
        except genai_errors.ClientError as exc:
            if exc.code in (408, 429):
                raise LLMUnavailableError(f"gemini client error {exc.code}") from exc
            raise LLMError(f"gemini client error {exc.code}") from exc
        except (TimeoutError, OSError) as exc:
            raise LLMUnavailableError("gemini unreachable") from exc

        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise LLMBlockedError(str(response.prompt_feedback.block_reason))
        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else None
        if finish in _BLOCKED_FINISH_REASONS:
            raise LLMBlockedError(finish)
        usage = response.usage_metadata
        return LLMResponse(
            text=response.text or "",
            model=response.model_version or self.model,
            usage=LLMUsage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(
                    (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                )
                if usage
                else 0,
            ),
            finish_reason=finish,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._call(request)

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]:
        response = await self._call(request, schema)
        return parse_structured(schema, response.text), response
