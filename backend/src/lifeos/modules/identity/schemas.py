import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegistrationOptionsRequest(BaseModel):
    enrollment_token: str | None = Field(default=None, max_length=128)


class CeremonyOptions(BaseModel):
    ceremony_id: uuid.UUID
    public_key: dict[str, Any]


class RegistrationVerifyRequest(BaseModel):
    ceremony_id: uuid.UUID
    credential: dict[str, Any]
    label: str = Field(default="Passkey", max_length=80)


class AuthenticationVerifyRequest(BaseModel):
    ceremony_id: uuid.UUID
    credential: dict[str, Any]


class SpaceOut(ORMModel):
    id: uuid.UUID
    kind: str
    name: str


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str
    locale: str
    timezone: str


class SessionInfo(ORMModel):
    id: uuid.UUID
    authenticated_at: datetime
    expires_at: datetime


class MeOut(BaseModel):
    user: UserOut
    spaces: list[SpaceOut]
    session: SessionInfo


class SessionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    authenticated_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: IPvAnyAddress | None
    current: bool = False


class PasskeyOut(ORMModel):
    id: uuid.UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None
    backup_eligible: bool
    backup_state: bool


class PasskeyUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
