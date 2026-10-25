"""API shapes of the connectors. No token, sealed or not, ever appears here."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from lifeos.modules.connectors.base import Capability


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProviderOut(BaseModel):
    name: str
    label: str
    capabilities: list[Capability]


class GrantOut(ORMModel):
    id: uuid.UUID
    space_id: uuid.UUID
    capability: str
    created_at: datetime
    expires_at: datetime | None


class SourceOut(ORMModel):
    id: uuid.UUID
    space_id: uuid.UUID
    kind: str
    name: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None


class AccountOut(BaseModel):
    id: uuid.UUID
    provider: str
    email: str | None
    status: str
    # What the provider granted (scopes); `grants` says where each may be used.
    capabilities: list[str]
    grants: list[GrantOut]
    sources: list[SourceOut]
    connected_at: datetime
    revoked_at: datetime | None
    last_error: str | None


class OAuthStartIn(BaseModel):
    provider: str = Field(max_length=32)
    space_id: uuid.UUID
    # At least one, except for a reconnection (the accesses the account already held).
    capabilities: list[Capability] = Field(default_factory=list, max_length=4)
    # Incremental consent, or reconnection, of an account already linked.
    account_id: uuid.UUID | None = None
    # Reconnection: new tokens for `account_id`, no new consent in any space.
    reconnect: bool = False


class OAuthStartOut(BaseModel):
    authorization_url: str


class OAuthCallbackIn(BaseModel):
    state: str = Field(min_length=1, max_length=512)
    code: str | None = Field(default=None, max_length=4096)
    # The provider's error (e.g. access_denied when the owner refused).
    error: str | None = Field(default=None, max_length=128)


class OAuthCallbackOut(BaseModel):
    account: AccountOut
    space_id: uuid.UUID
    requested: list[Capability]
    granted: list[Capability]
    # Requested but unticked by the owner on the provider's page.
    missing: list[Capability]
    # Reconnection: tokens restored, consents per space unchanged (none added).
    reconnected: bool


class GrantsIn(BaseModel):
    space_id: uuid.UUID
    capabilities: list[Capability] = Field(max_length=4)


class DisconnectOut(BaseModel):
    account_id: uuid.UUID
    # "failed" or "manual_required": withdraw LifeOS's access in the account's own
    # security settings ("manual_required": tokens already wiped, nothing to revoke with).
    remote_revocation: str
    purged: bool


class CalendarOut(BaseModel):
    """A calendar of the account, read live at the provider (task 14)."""

    provider_ref: str
    name: str
    primary: bool
    read_only: bool
    # Already mapped: the space it feeds.
    source_id: uuid.UUID | None = None
    space_id: uuid.UUID | None = None


class SourceIn(BaseModel):
    account_id: uuid.UUID
    provider_ref: str = Field(min_length=1, max_length=1024)
    space_id: uuid.UUID
