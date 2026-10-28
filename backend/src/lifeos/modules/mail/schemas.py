"""API shapes of the mail (read). Every text comes from third parties: data only, shown as
plain text (T14)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


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
