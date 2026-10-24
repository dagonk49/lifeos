import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from lifeos.api.deps import CtxDep
from lifeos.modules.connectors import Connectors, oauth, service
from lifeos.modules.connectors.base import CAPABILITIES
from lifeos.modules.connectors.schemas import (
    AccountOut,
    DisconnectOut,
    GrantOut,
    GrantsIn,
    OAuthCallbackIn,
    OAuthCallbackOut,
    OAuthStartIn,
    OAuthStartOut,
    ProviderOut,
    SourceOut,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def get_connectors(request: Request) -> Connectors:
    connectors: Connectors = request.app.state.connectors
    return connectors


ConnectorsDep = Annotated[Connectors, Depends(get_connectors)]


def _account_out(view: service.AccountView) -> AccountOut:
    account = view.account
    return AccountOut(
        id=account.id,
        provider=account.provider,
        email=account.email,
        status=account.status,
        capabilities=view.capabilities,
        grants=[GrantOut.model_validate(g) for g in view.grants],
        sources=[SourceOut.model_validate(s) for s in view.sources],
        connected_at=account.connected_at,
        revoked_at=account.revoked_at,
        last_error=account.last_error,
    )


@router.get("/providers")
async def list_providers(ctx: CtxDep, connectors: ConnectorsDep) -> list[ProviderOut]:
    """Providers configured on this server (none: connectors disabled)."""
    return [
        ProviderOut(name=p.name, label=p.label, capabilities=list(CAPABILITIES))
        for p in connectors.values()
    ]


@router.get("/accounts")
async def list_accounts(ctx: CtxDep, connectors: ConnectorsDep) -> list[AccountOut]:
    return [_account_out(v) for v in await service.list_accounts(ctx, connectors)]


@router.post("/oauth/start")
async def start_oauth(body: OAuthStartIn, ctx: CtxDep, connectors: ConnectorsDep) -> OAuthStartOut:
    url = await oauth.start(
        ctx,
        connectors,
        provider_name=body.provider,
        space_id=body.space_id,
        capabilities=list(body.capabilities),
        account_id=body.account_id,
        reconnect=body.reconnect,
    )
    return OAuthStartOut(authorization_url=url)


@router.post("/oauth/callback")
async def oauth_callback(
    body: OAuthCallbackIn, ctx: CtxDep, connectors: ConnectorsDep
) -> OAuthCallbackOut:
    result = await oauth.complete(
        ctx, connectors, state=body.state, code=body.code, error=body.error
    )
    view = await service.account_view(ctx, connectors, result.account)
    return OAuthCallbackOut(
        account=_account_out(view),
        space_id=result.space_id,
        requested=list(result.requested),
        granted=list(result.granted),
        missing=list(result.missing),
        reconnected=result.reconnected,
    )


@router.put("/accounts/{account_id}/grants")
async def set_grants(
    account_id: uuid.UUID, body: GrantsIn, ctx: CtxDep, connectors: ConnectorsDep
) -> AccountOut:
    account = await service.set_grants(
        ctx, connectors, account_id, body.space_id, list(body.capabilities)
    )
    return _account_out(await service.account_view(ctx, connectors, account))


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(grant_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.revoke_grant(ctx, grant_id)


@router.delete("/accounts/{account_id}")
async def disconnect(
    account_id: uuid.UUID,
    ctx: CtxDep,
    connectors: ConnectorsDep,
    purge: Annotated[bool, Query(description="Also delete the synced data")] = False,
) -> DisconnectOut:
    result = await service.disconnect(ctx, connectors, account_id, purge=purge)
    return DisconnectOut(
        account_id=result.account_id,
        remote_revocation=result.remote_revocation,
        purged=result.purged,
    )
