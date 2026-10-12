"""Voice (UC01): dictations transcribed locally. Speech synthesis (reading answers aloud)
happens in the browser and needs no server route."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Request, status

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError
from lifeos.core.uploads import read_upload
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.voice import service
from lifeos.modules.voice.schemas import DictationFields, DictationOut, VoiceStatus
from lifeos.workers.queue import enqueue

router = APIRouter(prefix="/voice", tags=["voice"])

DICTATION_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "space_id"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "space_id": {"type": "string", "format": "uuid"},
                    },
                }
            }
        },
    }
}


@router.get("/status")
async def voice_status(ctx: CtxDep) -> VoiceStatus:
    return VoiceStatus(
        transcription=ctx.settings.stt_provider != "disabled",
        max_seconds=ctx.settings.voice_max_seconds,
    )


@router.post(
    "/dictations",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=DICTATION_OPENAPI,
)
async def create_dictation(
    request: Request, ctx: CtxDep, background: BackgroundTasks
) -> DictationOut:
    """Multipart upload of a recording (webm/ogg/mp4/wav/mp3). Transcribed in the
    background: poll `GET /v1/voice/dictations/{id}` until it is ready."""
    if ctx.settings.stt_provider == "disabled":
        raise ProblemError(503, "transcription_unavailable", "Voice transcription is disabled")
    store = DocumentStore(ctx.settings)
    if not store.available:
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable")
    upload = await read_upload(request, ctx.settings.documents_max_bytes, DictationFields)
    detected = sniff(upload.data)
    if detected is None or detected.kind != "audio":
        raise ProblemError(415, "unsupported_type", "Expected an audio recording")
    document = await service.create_dictation(
        ctx, store, upload.fields.space_id, data=upload.data, detected=detected
    )
    # After the response, hence after the commit. A lost message is harmless: the
    # periodic job transcribes waiting dictations.
    background.add_task(enqueue, "transcribe_dictations", queue="stt")
    return service.out(document)


@router.get("/dictations/{dictation_id}")
async def get_dictation(dictation_id: uuid.UUID, ctx: CtxDep) -> DictationOut:
    return service.out(await service.get_dictation(ctx, dictation_id))


@router.delete("/dictations/{dictation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dictation(dictation_id: uuid.UUID, ctx: CtxDep) -> None:
    """Called by the client once the owner used (or discarded) the transcript."""
    await service.delete_dictation(ctx, DocumentStore(ctx.settings), dictation_id)
