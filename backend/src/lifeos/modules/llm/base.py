"""Provider-agnostic LLM contract (ADR 0003).

Business modules never call a provider directly: they go through the Guardian cloud
gateway, which authorises, minimises, masks and meters every call.
"""

from typing import Literal, Protocol

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


Tier = Literal["fast", "deep"]


class LLMRequest(BaseModel):
    purpose: str = Field(description="Why the call is made, e.g. 'chat.reply' (audit, budget)")
    system: str | None = None
    messages: list[LLMMessage]
    temperature: float | None = None
    max_output_tokens: int | None = None
    # "fast": cheap model, minimal thinking. "deep": better model for long syntheses.
    tier: Tier = "fast"
    # Background jobs may accept slower, cheaper processing (Flex tier when paid).
    background: bool = False


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    model: str
    usage: LLMUsage
    finish_reason: str | None = None


class LLMError(Exception):
    """Base error. `retryable` tells callers whether a later retry may succeed."""

    retryable = False


class LLMUnavailableError(LLMError):
    """Provider disabled, unreachable, rate limited or failing."""

    retryable = True


class LLMDisabledError(LLMUnavailableError):
    retryable = False


class LLMQuotaError(LLMUnavailableError):
    """The provider's rate limit or quota is exhausted (HTTP 429)."""


class LLMBillingError(LLMUnavailableError):
    """Prepaid balance exhausted or spend cap reached (HTTP 402): never retried."""

    retryable = False


class LLMBlockedError(LLMError):
    """The provider refused to answer (safety filters, policy)."""


class LLMInvalidOutputError(LLMError):
    """Structured output did not validate against the expected schema."""


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]: ...


def parse_structured[T: BaseModel](schema: type[T], text: str) -> T:
    try:
        return schema.model_validate_json(text)
    except ValueError as exc:
        raise LLMInvalidOutputError(f"output does not match {schema.__name__}") from exc
