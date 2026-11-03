"""API shapes of the mail (read). Every text comes from third parties: data only, shown as
plain text (T14)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MailAttachmentOut(BaseModel):
    part_id: str
    filename: str
    mime: str
    size: int
    # Imported into Documents by the owner (never automatically).
    document_id: uuid.UUID | None = None


class MailSummary(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    source_id: uuid.UUID
    # Where it comes from: the label's name.
    source_name: str
    from_name: str | None
    from_address: str | None
    subject: str
    # The start of the text (plain text).
    snippet: str
    received_at: datetime
    attachments: int
    # Categories detected locally: such a message never goes to the cloud.
    sensitivity: list[str]


class MailOut(MailSummary):
    to_addresses: list[str]
    cc_addresses: list[str]
    body_text: str
    body_truncated: bool
    attachment_list: list[MailAttachmentOut]
    synced_at: datetime | None


# --- Sending (task 18) -------------------------------------------------------------------------


class DraftIn(BaseModel):
    """A new draft. Addresses are checked and written in lower case; nothing is sent."""

    space_id: uuid.UUID
    # None: the first account allowed to send in this space.
    account_id: uuid.UUID | None = None
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    bcc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=200_000)
    # Documents of the same space, ready, with their original kept.
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    in_reply_to_id: uuid.UUID | None = None


class DraftPatch(BaseModel):
    """A new version (If-Match: the version read). Its pending approval is invalidated."""

    account_id: uuid.UUID | None = None
    to: list[str] | None = Field(default=None, max_length=50)
    cc: list[str] | None = Field(default=None, max_length=50)
    bcc: list[str] | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=1000)
    body: str | None = Field(default=None, max_length=200_000)
    attachment_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


class DraftAttachmentOut(BaseModel):
    document_id: uuid.UUID
    title: str | None
    mime: str | None
    size_bytes: int | None
    sensitivity: list[str]
    local_only: bool
    # False when the document was deleted since.
    available: bool


DraftState = Literal["draft", "awaiting_approval", "sending", "unknown", "failed", "sent"]


class DraftOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    account_id: uuid.UUID | None
    from_address: str | None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    attachments: list[DraftAttachmentOut]
    in_reply_to_id: uuid.UUID | None
    reply: bool
    state: DraftState
    approval_id: uuid.UUID | None
    approval_level: str | None
    # Why the last sending failed, or is uncertain (stable code).
    error: str | None
    sent_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class SendAccountOut(BaseModel):
    id: uuid.UUID
    email: str | None
    provider: str


class ResolveIn(BaseModel):
    """The owner checked their sent messages: did this e-mail leave?"""

    sent: bool
