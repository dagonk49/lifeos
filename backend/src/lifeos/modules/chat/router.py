import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.chat import service
from lifeos.modules.chat.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    RetainRequest,
    SendMessage,
    SendResult,
)
from lifeos.modules.memory.schemas import MemoryOut

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations")
async def list_conversations(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[ConversationOut]:
    return await service.list_conversations(ctx, space_id=space_id, limit=limit, cursor=cursor)


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationCreate, ctx: CtxDep) -> ConversationOut:
    return ConversationOut.model_validate(await service.create_conversation(ctx, body))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, ctx: CtxDep) -> ConversationDetail:
    conversation = await service.get_conversation(ctx, conversation_id)
    messages = await service.list_messages(ctx, conversation)
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationUpdate, ctx: CtxDep
) -> ConversationOut:
    return ConversationOut.model_validate(
        await service.rename_conversation(ctx, conversation_id, body.title)
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_conversation(ctx, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=SendResult,
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessage,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> SendResult:
        return await service.send_message(ctx, conversation_id, body)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.post("/messages/{message_id}/retain")
async def retain_message(message_id: uuid.UUID, body: RetainRequest, ctx: CtxDep) -> MessageOut:
    return MessageOut.model_validate(await service.set_retained(ctx, message_id, body.retained))


@router.post("/messages/{message_id}/remember", status_code=status.HTTP_201_CREATED)
async def remember_message(message_id: uuid.UUID, ctx: CtxDep) -> MemoryOut:
    return MemoryOut.model_validate(await service.remember_message(ctx, message_id))


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(message_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_message(ctx, message_id)
