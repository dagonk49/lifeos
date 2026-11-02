import uuid
from datetime import datetime

from pydantic import BaseModel


class DictationFields(BaseModel):
    """Form fields of `POST /v1/voice/dictations` (multipart/form-data, next to `file`)."""

    space_id: uuid.UUID


class DictationOut(BaseModel):
    """A dictation: the recording is transcribed on the server, then deleted. The text is
    shown to the owner, who corrects it and decides where it goes (journal, chat...)."""

    id: uuid.UUID
    space_id: uuid.UUID
    # pending | processing | ready | failed
    status: str
    # Machine-readable reason of a pending (retry) or failed status.
    error: str | None
    # The transcript once ready ("" when nothing was said).
    text: str | None
    # Length of the recording, in seconds, once transcribed.
    duration: float | None
    # While transcribed: the share of the recording done, in [0, 1] (coarse steps).
    progress: float | None = None
    # While waiting or transcribed: dictations of the owner ahead of this one (0: it is
    # the next, or the current one).
    queue_position: int | None = None
    # While transcribed, once the service knows the recording's length: estimated seconds
    # left, from the dictations measured on this machine (else LIFEOS_VOICE_RTF_ESTIMATE).
    estimated_seconds: float | None = None
    # Kept until then at most, unless deleted earlier by the client once used.
    expires_at: datetime
    created_at: datetime


class VoiceStatus(BaseModel):
    transcription: bool
    max_seconds: int
