"""Gemini adapter (google-genai SDK), for the Gemini Developer API or Vertex AI.

Cost-related choices (ADR 0007): two tiers ("fast" for chat and extraction, "deep" for
long syntheses) with explicit thinking levels, no automatic SDK retries (a retry is a
billed request), Flex service tier for background jobs on paid billing, and explicit
errors for exhausted quotas (429) or prepaid balance (402).
"""

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from lifeos.config import Settings
from lifeos.modules.llm.base import (
    LLMBillingError,
    LLMBlockedError,
    LLMError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMQuotaError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    LLMUsage,
    parse_structured,
)
from lifeos.modules.llm.schema import gemini_schema

_BLOCKED_FINISH_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}
_THINKING = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}


def _to_contents(messages: list[LLMMessage]) -> types.ContentListUnion:
    contents: list[types.ContentUnion] = [
        types.Content(
            role="model" if m.role == "assistant" else "user",
            parts=[types.Part.from_text(text=m.text)],
        )
        for m in messages
    ]
    return contents


def _is_thinking_level_error(exc: genai_errors.ClientError) -> bool:
    message = str(exc).lower()
    return exc.code == 400 and "thinking" in message


def _seconds(duration: object) -> int | None:
    if isinstance(duration, str) and duration.endswith("s"):
        try:
            return int(float(duration[:-1]))
        except ValueError:
            return None
    return None


def quota_error(exc: genai_errors.ClientError) -> LLMQuotaError:
    """Classifies a 429: daily quota / spend cap (terminal) or short-term rate limit."""
    message = str(exc)
    daily = "limit: 0" in message or "spending cap" in message
    retry_after = None
    details = (
        exc.details.get("error", {}).get("details", []) if isinstance(exc.details, dict) else []
    )
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in detail.get("violations", []) or []:
                quota_id = str(violation.get("quotaId", ""))
                if "PerDay" in quota_id or "Daily" in quota_id:
                    daily = True
        elif kind.endswith("RetryInfo"):
            retry_after = _seconds(detail.get("retryDelay"))
    if retry_after is not None and retry_after > 300:
        daily = True
    return LLMQuotaError("gemini quota exhausted", daily=daily, retry_after=retry_after)


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        assert settings.gemini_model is not None  # enforced by Settings validation
        self.models = {
            "fast": settings.gemini_model,
            "deep": settings.gemini_model_deep or settings.gemini_model,
        }
        self.thinking = {
            "fast": settings.gemini_thinking_fast,
            "deep": settings.gemini_thinking_deep,
        }
        self.flex_for_background = (
            settings.llm_billing == "paid" and settings.gemini_flex_for_background
        )
        # Retries are disabled on purpose (retry_options=None): every attempt is billed
        # and counts against quotas; the caller decides what to do on failure.
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

    @property
    def model(self) -> str:
        return self.models["fast"]

    def _config(
        self, request: LLMRequest, schema: type[BaseModel] | None, thinking: str
    ) -> types.GenerateContentConfig:
        # No temperature: Gemini 3.x models ignore it (and may reject it); thinking level
        # is the lever for cost and latency.
        return types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_output_tokens,
            candidate_count=1,
            response_mime_type="application/json" if schema else None,
            response_json_schema=gemini_schema(schema.model_json_schema()) if schema else None,
            thinking_config=types.ThinkingConfig(
                thinking_level=_THINKING[thinking], include_thoughts=False
            ),
            service_tier=types.ServiceTier.FLEX
            if request.background and self.flex_for_background
            else None,
        )

    async def _send(
        self, request: LLMRequest, schema: type[BaseModel] | None, thinking: str
    ) -> types.GenerateContentResponse:
        return await self._client.aio.models.generate_content(
            model=self.models[request.tier],
            contents=_to_contents(request.messages),
            config=self._config(request, schema, thinking),
        )

    async def _call(
        self, request: LLMRequest, schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        thinking = self.thinking[request.tier]
        try:
            try:
                response = await self._send(request, schema, thinking)
            except genai_errors.ClientError as exc:
                # Some models reject "minimal" (e.g. Flash 3.8): a 400 is not billed,
                # retry with the lowest level they accept, and remember it.
                if thinking != "minimal" or not _is_thinking_level_error(exc):
                    raise
                response = await self._send(request, schema, "low")
                self.thinking[request.tier] = "low"
        except genai_errors.ServerError as exc:
            raise LLMUnavailableError(f"gemini server error {exc.code}") from exc
        except genai_errors.ClientError as exc:
            if exc.code == 402:
                raise LLMBillingError("gemini prepaid balance or spend cap exhausted") from exc
            if exc.code == 429:
                raise quota_error(exc) from exc
            if exc.code == 408:
                raise LLMUnavailableError("gemini timeout") from exc
            raise LLMError(f"gemini client error {exc.code}") from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMUnavailableError("gemini timeout") from exc
        except (OSError, httpx.TransportError) as exc:
            raise LLMUnavailableError("gemini unreachable") from exc

        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else None
        usage = response.usage_metadata
        result = LLMResponse(
            text=response.text or "",
            model=response.model_version or self.models[request.tier],
            usage=LLMUsage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                cached_input_tokens=(usage.cached_content_token_count or 0) if usage else 0,
                # Thinking tokens are billed as output tokens.
                output_tokens=(
                    (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                )
                if usage
                else 0,
            ),
            finish_reason=finish,
        )
        # Unusable answers are still billed: the error carries the usage for metering.
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise LLMBlockedError(str(response.prompt_feedback.block_reason)).with_response(result)
        if finish in _BLOCKED_FINISH_REASONS:
            raise LLMBlockedError(finish).with_response(result)
        if finish == "MAX_TOKENS" and not response.text:
            # Thinking used the whole output budget: nothing usable came back.
            raise LLMInvalidOutputError("gemini answer truncated (MAX_TOKENS)").with_response(
                result
            )
        return result

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._call(request)

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]:
        response = await self._call(request, schema)
        return parse_structured(schema, response.text, response), response
