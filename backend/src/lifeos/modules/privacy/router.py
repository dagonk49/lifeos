"""Export of the owner's data (docs/politique-de-donnees.md §2).

- `GET /v1/privacy/export`: the complete JSON document.
- `GET /v1/privacy/export.zip`: the same JSON, with CSV, iCalendar and Markdown copies and
  a README. `?originals=true` adds the decrypted originals of the documents.

Both are reinforced (`data.export`: recent passkey sign-in) and audited; agents never
export (no agent level). Every row is selected by its owner (privacy/export.py
`owner_filter`).
"""

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.privacy import archive
from lifeos.modules.privacy.export import Export, collect

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _attachment(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, no-store",
    }


async def _audit(ctx: Ctx, data: Export, export_format: str, originals: int | None) -> None:
    details: dict[str, str | int | float | bool | None] = {
        "format": export_format,
        "rows": data.count(),
    }
    if originals is not None:
        details["originals"] = originals
    await audit.record(
        ctx.db,
        action="privacy.export",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        details=details,
    )


@router.get("/export", response_class=Response)
async def export_json(ctx: CtxDep) -> Response:
    await authorize(ctx, "data.export", None)
    data = await collect(ctx)
    await _audit(ctx, data, "json", None)
    return Response(
        content=data.to_json(),
        media_type="application/json",
        headers=_attachment(f"{archive.root_name(data.exported_at)}.json"),
    )


@router.get(
    "/export.zip",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
async def export_archive(ctx: CtxDep, originals: bool = False) -> StreamingResponse:
    await authorize(ctx, "data.export", None)
    data = await collect(ctx)
    store = DocumentStore(ctx.settings)
    planned: list[archive.Original] | None = None
    withheld = 0
    if originals:
        documents = await ctx.db.scalars(
            select(Document)
            .where(Document.user_id == ctx.principal.user_id)
            .order_by(Document.created_at, Document.id)
        )
        planned, withheld = archive.plan_originals(documents)
        if planned and not store.available:
            raise ProblemError(503, "documents_unavailable", "Document storage is unavailable")
    await _audit(ctx, data, "zip", len(planned) if planned is not None else None)
    return StreamingResponse(
        archive.stream(data, store=store, originals=planned, withheld=withheld),
        media_type="application/zip",
        headers=_attachment(f"{archive.root_name(data.exported_at)}.zip"),
    )
