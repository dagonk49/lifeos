import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from lifeos.modules.chat.attachments import MAX_ATTACHMENTS
from lifeos.modules.mail.schemas import DraftOut

# E-mails attached to one message at most (lifeos.modules.mail.service.MAX_MAILS).
MAX_MAILS = 3


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


# What the owner may explicitly attach to a message as its subject (task 24): a closed list,
# mirrored by chat.models.CONTEXT_TYPES (CHECK constraint).
ContextType = Literal["task", "goal", "journal", "review"]


class ContextRef(BaseModel):
    """One object of the conversation's space, chosen by the owner (« Discuter de… »).

    A reference only: the server loads it with the replying principal's rights, in the
    conversation's space, and sends it as delimited data (never as instructions).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)
    type: ContextType
    id: uuid.UUID


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
    # Task, goal, journal entry or review the owner explicitly chose for this message
    # (task 24): a reference only.
    context: ContextRef | None = None
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

    model_config = ConfigDict(extra="forbid")
    screen: Screen | None = None
    # Sent with this message only: later messages do not carry it unless sent again.
    ref: ContextRef | None = None


# Every agent of lifeos.modules.agents.registry.AGENT_SPECS, plus the general assistant
# (kept in sync by tests/test_agents.py).
AgentChoice = Literal["core", "planning", "coaching", "memory", "finance", "projects", "documents"]
MAX_CONTENT_CHARS = 8000


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)
    context: ChatContext = Field(default_factory=ChatContext)
    # Specialised agents are only used when explicitly requested (F08).
    agent: AgentChoice = "core"
    # Documents whose locally extracted text goes with the message (never sensitive ones).
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    # E-mails the owner attaches to this message (task 17): sent as delimited data with
    # it only, never if sensitive nor if the space has no cloud AI consent.
    mail_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_MAILS)

    @field_validator("attachments", "mail_ids")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class PreviewRequest(BaseModel):
    """What the owner is about to send: checked locally, nothing is saved or sent."""

    space_id: uuid.UUID
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)
    agent: AgentChoice = "core"
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    # E-mails the owner attaches to this message (task 17): sent as delimited data with
    # it only, never if sensitive nor if the space has no cloud AI consent.
    mail_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_MAILS)

    @field_validator("attachments", "mail_ids")
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


class AgentDraftRequest(BaseModel):
    """Asks an agent of the conversation for a draft (nothing is sent, ever)."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentChoice = "core"
    # The e-mail answered (same space as the conversation): its text goes to the model as
    # data, its sender becomes the recipient, the reply is threaded.
    in_reply_to_id: uuid.UUID | None = None
    # A new version of a draft this agent proposed in this conversation, untouched by
    # the owner.
    draft_id: uuid.UUID | None = None


class AgentDraftOut(BaseModel):
    draft: DraftOut
    # Addresses proposed by the model that were not valid (masked, malformed): left out.
    dropped_recipients: int = 0


class RetainRequest(BaseModel):
    retained: bool
