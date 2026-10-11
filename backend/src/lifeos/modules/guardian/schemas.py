import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from lifeos.modules.guardian.policy import ACTIONS


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApprovalOut(ORMModel):
    id: uuid.UUID
    space_id: uuid.UUID
    action: str
    params: dict[str, Any]
    preview: dict[str, Any]
    level: str
    status: str
    requested_by: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    result: dict[str, Any] | None
    error: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def description(self) -> str:
        spec = ACTIONS.get(self.action)
        return spec.description if spec else self.action


class ApproveRequest(BaseModel):
    # The owner may edit the exact parameters before approving them.
    params: dict[str, Any] | None = None


class PermissionIn(BaseModel):
    agent: str
    space_id: uuid.UUID
    resource: str
    access: str = "read"
    purpose: str = Field(min_length=3, max_length=200)
    expires_at: datetime | None = None


class PermissionOut(ORMModel):
    id: uuid.UUID
    agent: str
    space_id: uuid.UUID
    resource: str
    access: str
    purpose: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AuditEventOut(ORMModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    space_id: uuid.UUID | None
    details: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None = None


class LLMCallOut(ORMModel):
    id: uuid.UUID
    space_id: uuid.UUID | None
    purpose: str
    provider: str
    tier: str
    model: str | None
    cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    redactions: int
    outcome: str
    error_code: str | None
    duration_ms: int
    created_at: datetime


class LLMUsageOut(BaseModel):
    provider: str
    enabled: bool
    paused: bool
    billing: str
    models: dict[str, str | None]
    requests_today: dict[str, int]
    daily_request_caps: dict[str, int]
    quota_resets_at: datetime
    month_tokens_used: int
    month_token_budget: int
    cost_today_usd: str
    cost_month_usd: str
    daily_budget_usd: float
    monthly_budget_usd: float
    recent_calls: list[LLMCallOut]


class ActionOut(BaseModel):
    name: str
    description: str
    user_level: str
    agent_level: str | None
