"""Deterministic provider for tests and offline development. Sends nothing anywhere."""

from collections import deque
from collections.abc import Callable
from contextlib import suppress

from pydantic import BaseModel

from lifeos.modules.llm.base import (
    LLMDisabledError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    parse_structured,
)

Responder = Callable[[LLMRequest], str]


def _default_responder(request: LLMRequest) -> str:
    last = request.messages[-1].text if request.messages else ""
    return f"[fake] {last[:200]}"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeProvider:
    name = "fake"

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or _default_responder
        self.queue: deque[str | LLMError] = deque()
        self.requests: list[LLMRequest] = []

    def push(self, *items: str | LLMError) -> None:
        """Queue scripted replies (or errors) consumed before the responder."""
        self.queue.extend(items)

    def _reply(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self.queue.popleft() if self.queue else self.responder(request)
        if isinstance(item, LLMError):
            raise item
        prompt = (request.system or "") + "".join(m.text for m in request.messages)
        return LLMResponse(
            text=item,
            model="fake-1",
            usage=LLMUsage(
                input_tokens=_estimate_tokens(prompt), output_tokens=_estimate_tokens(item)
            ),
            finish_reason="STOP",
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return self._reply(request)

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]:
        if not self.queue and self.responder is _default_responder:
            # Offline development: the smallest valid answer (e.g. "nothing to propose").
            with suppress(ValueError):
                self.push(schema.model_validate({}).model_dump_json())
        response = self._reply(request)
        return parse_structured(schema, response.text), response


class DisabledProvider:
    name = "disabled"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMDisabledError("no LLM provider configured")

    async def generate_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]:
        raise LLMDisabledError("no LLM provider configured")
