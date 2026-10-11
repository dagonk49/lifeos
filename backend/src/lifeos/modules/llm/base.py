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
    # Part of input_tokens served from the provider's (implicit) cache, billed ~10 %.
    cached_input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    model: str
    usage: LLMUsage
    finish_reason: str | None = None


class LLMError(Exception):
    """Base error. `retryable` tells callers whether a later retry may succeed.

    `response` is set when the provider did answer (and billed tokens) but the answer is
    unusable (blocked, truncated, invalid): the gateway meters its usage anyway.
    """

    retryable = False
    response: "LLMResponse | None" = None

    def with_response(self, response: "LLMResponse") -> "LLMError":
        self.response = response
        return self


class LLMUnavailableError(LLMError):
    """Provider disabled, unreachable, rate limited or failing."""

    retryable = True


class LLMDisabledError(LLMUnavailableError):
    retryable = False


class LLMQuotaError(LLMUnavailableError):
    """The provider's rate limit or quota is exhausted (HTTP 429)."""

    def __init__(self, message: str, *, daily: bool = False, retry_after: int | None = None):
        super().__init__(message)
        # A daily quota only resets at midnight Pacific: do not retry before.
        self.daily = daily
        self.retry_after = retry_after


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


def parse_structured[T: BaseModel](
    schema: type[T], text: str, response: "LLMResponse | None" = None
) -> T:
    try:
        return schema.model_validate_json(text)
    except ValueError as exc:
        error = LLMInvalidOutputError(f"output does not match {schema.__name__}")
        if response is not None:
            error.with_response(response)
        raise error from exc
