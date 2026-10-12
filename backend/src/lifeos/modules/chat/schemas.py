import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from lifeos.modules.chat.attachments import MAX_ATTACHMENTS


class ConversationCreate(BaseModel):
    space_id: uuid.UUID
    title: str | None = Field(default=None, max_length=120)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    last_message_at: datetime | None
    consolidated_until: datetime | None
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str
    model: str | None
    agent: str | None = None
    # Documents attached by the owner (their extracted text was sent with the message).
    attachments: list[uuid.UUID] = []
    purge_after: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retained(self) -> bool:
        return self.purge_after is None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ChatContext(BaseModel):
    """Interface context explicitly selected by the client (never implicit data)."""

    screen: str | None = Field(default=None, max_length=40)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    context: ChatContext = Field(default_factory=ChatContext)
    # Specialised agents are only used when explicitly requested (F08).
    agent: Literal["core", "planning", "coaching", "memory", "finance"] = "core"
    # Documents whose locally extracted text goes with the message (never sensitive ones).
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @field_validator("attachments")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class SendResult(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut | None
    # Degraded mode: the message is saved, the reply is unavailable and says why.
    error: dict[str, Any] | None = None
    context_items: int = 0
    agent: str = "core"
    # Deterministic hint shown by the client: an agent that could help next time.
    suggested_agent: str | None = None


class RetainRequest(BaseModel):
    retained: bool
