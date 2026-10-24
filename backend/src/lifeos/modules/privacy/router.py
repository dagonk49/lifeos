"""Complete export of the owner's data (JSON).

Documents are exported as metadata and extracted text: the encrypted originals are
downloaded one by one (`GET /v1/documents/{id}/original`), never bundled here.
"""

from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select

from lifeos import __version__
from lifeos.api.deps import CtxDep
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource
from lifeos.modules.documents.models import Document
from lifeos.modules.finance.models import CategoryRule, FinanceCategory, Transaction
from lifeos.modules.guardian.models import AgentPermission
from lifeos.modules.identity.models import Space
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory, MemoryRevision
from lifeos.modules.tasks.models import Task

router = APIRouter(prefix="/privacy", tags=["privacy"])

_SKIP = {"search_vector", "user_id"}


def _row(obj: Any) -> dict[str, Any]:
    return {
        column.key: getattr(obj, column.key)
        for column in obj.__mapper__.column_attrs
        if column.key not in _SKIP
    }


@router.get("/export")
async def export(ctx: CtxDep) -> JSONResponse:
    uid = ctx.principal.user_id
    spaces = ctx.principal.space_ids

    async def rows(model: Any) -> list[dict[str, Any]]:
        result = await ctx.db.scalars(
            select(model)
            .where(model.user_id == uid, model.space_id.in_(spaces))
            .order_by(model.created_at)
        )
        return [_row(r) for r in result]

    memories = await rows(Memory)
    revisions = await ctx.db.scalars(
        select(MemoryRevision).where(MemoryRevision.memory_id.in_([m["id"] for m in memories]))
    )
    by_memory: dict[Any, list[dict[str, Any]]] = {}
    for revision in revisions:
        by_memory.setdefault(revision.memory_id, []).append(_row(revision))
    for memory in memories:
        memory["revisions"] = by_memory.get(memory["id"], [])

    documents = await rows(Document)
    for document in documents:
        # Where the encrypted original lies on the volume is internal, not personal data.
        document["original_available"] = document.pop("storage_key") is not None

    permissions = await ctx.db.scalars(
        select(AgentPermission).where(AgentPermission.user_id == uid)
    )
    space_rows = await ctx.db.scalars(select(Space).where(Space.id.in_(spaces)))
    # Connected accounts: what was authorised, where. Never the (sealed) tokens.
    accounts = []
    for account in await ctx.db.scalars(
        select(ConnectorAccount)
        .where(ConnectorAccount.user_id == uid)
        .order_by(ConnectorAccount.created_at)
    ):
        row = _row(account)
        row.pop("refresh_token_enc")
        row.pop("access_token_enc")
        accounts.append(row)
    connector_grants = await ctx.db.scalars(
        select(ConnectorGrant)
        .where(ConnectorGrant.user_id == uid, ConnectorGrant.space_id.in_(spaces))
        .order_by(ConnectorGrant.created_at)
    )
    data = {
        "format": "lifeos-export",
        "version": 1,
        "app_version": __version__,
        "exported_at": utcnow(),
        "user": {
            "email": ctx.user.email,
            "display_name": ctx.user.display_name,
            "timezone": ctx.user.timezone,
            "locale": ctx.user.locale,
        },
        "spaces": [_row(s) for s in space_rows],
        "tasks": await rows(Task),
        "journal_entries": await rows(JournalEntry),
        "facts": await rows(Fact),
        "memories": memories,
        "conversations": await rows(Conversation),
        "messages": await rows(Message),
        "documents": documents,
        "finance_categories": await rows(FinanceCategory),
        "finance_category_rules": await rows(CategoryRule),
        "transactions": await rows(Transaction),
        "agent_permissions": [_row(p) for p in permissions],
        "connector_accounts": accounts,
        "connector_grants": [_row(g) for g in connector_grants],
        "connector_sources": await rows(ConnectorSource),
    }
    await audit.record(
        ctx.db,
        action="privacy.export",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=uid,
    )
    filename = f"lifeos-export-{utcnow():%Y%m%d}.json"
    return JSONResponse(
        jsonable_encoder(data),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
