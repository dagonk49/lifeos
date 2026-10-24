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


# The screens the web client sends the assistant from: a closed list, never free text
# placed in the prompt.
Screen = Literal["chat"]


class ChatContext(BaseModel):
    """Interface context explicitly selected by the client (never implicit data)."""

    screen: Screen | None = None


AgentChoice = Literal["core", "planning", "coaching", "memory", "finance"]
MAX_CONTENT_CHARS = 8000


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)
    context: ChatContext = Field(default_factory=ChatContext)
    # Specialised agents are only used when explicitly requested (F08).
    agent: AgentChoice = "core"
    # Documents whose locally extracted text goes with the message (never sensitive ones).
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @field_validator("attachments")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class PreviewRequest(BaseModel):
    """What the owner is about to send: checked locally, nothing is saved or sent."""

    space_id: uuid.UUID
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)
    agent: AgentChoice = "core"
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @field_validator("attachments")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class AttachmentPreview(BaseModel):
    document_id: uuid.UUID
    # False: the document stays on the server (`reason` says why).
    sent: bool
    # not_allowed, not_found, not_ready, sensitive, local_only, unreadable.
    reason: str | None = None
    # Categories found locally (reason "sensitive"): identity, health, bank.
    sensitivity: list[str] = []
    # Identifiers that will be masked in the attached text, by kind (never the values).
    masked: dict[str, int] = {}


class SendPreview(BaseModel):
    # Identifiers that will be masked in the message, by kind: email, phone, iban, card,
    # nir, secret, passport, id_number, tax_number.
    masked: dict[str, int]
    attachments: list[AttachmentPreview]


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
