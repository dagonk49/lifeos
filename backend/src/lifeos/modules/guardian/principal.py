"""Who is acting, and on which spaces."""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.guardian.models import AgentPermission
from lifeos.modules.identity.models import Space

Grant = tuple[uuid.UUID, str, str]  # (space_id, resource, access)


class SpaceAccessDeniedError(ProblemError):
    """A space outside the principal's, named by the request: 404 rather than 403 (do not
    reveal what exists elsewhere). Audited at the HTTP layer (lifeos.api.denials)."""

    def __init__(self, principal: "Principal", space_id: uuid.UUID) -> None:
        super().__init__(404, "space_not_found", "Space not found")
        self.actor_type = principal.kind
        self.actor_id = principal.actor_id
        self.user_id = principal.user_id
        self.space_id = space_id


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "agent", "system"]
    user_id: uuid.UUID
    actor_id: str
    space_ids: frozenset[uuid.UUID]
    agent: str | None = None
    recent_auth: bool = False
    # Agents only: explicit grants from `agent_permissions`.
    grants: frozenset[Grant] = field(default_factory=frozenset)

    def require_space(self, space_id: uuid.UUID) -> uuid.UUID:
        # 404 rather than 403: do not reveal what exists outside the allowed spaces.
        if space_id not in self.space_ids:
            raise SpaceAccessDeniedError(self, space_id)
        return space_id

    def spaces_or_all(self, space_id: uuid.UUID | None) -> frozenset[uuid.UUID]:
        if space_id is None:
            return self.space_ids
        return frozenset({self.require_space(space_id)})

    def can(self, space_id: uuid.UUID, resource: str, access: str) -> bool:
        if space_id not in self.space_ids:
            return False
        if self.kind != "agent":
            return True
        if (space_id, resource, access) in self.grants:
            return True
        # Write access implies read access.
        return access == "read" and (space_id, resource, "write") in self.grants


async def user_space_ids(session: AsyncSession, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    rows = await session.scalars(select(Space.id).where(Space.user_id == user_id))
    return frozenset(rows)


async def user_principal(
    session: AsyncSession, user_id: uuid.UUID, *, recent_auth: bool = False
) -> Principal:
    return Principal(
        kind="user",
        user_id=user_id,
        actor_id=str(user_id),
        space_ids=await user_space_ids(session, user_id),
        recent_auth=recent_auth,
    )


async def agent_principal(session: AsyncSession, user_id: uuid.UUID, agent: str) -> Principal:
    """An agent only sees the spaces and resources it was explicitly granted."""
    now = utcnow()
    rows = await session.scalars(
        select(AgentPermission).where(
            AgentPermission.user_id == user_id,
            AgentPermission.agent == agent,
            AgentPermission.revoked_at.is_(None),
            or_(AgentPermission.expires_at.is_(None), AgentPermission.expires_at > now),
        )
    )
    grants = frozenset((p.space_id, p.resource, p.access) for p in rows)
    return Principal(
        kind="agent",
        user_id=user_id,
        actor_id=agent,
        agent=agent,
        space_ids=frozenset(g[0] for g in grants),
        grants=grants,
    )


def system_principal(user_id: uuid.UUID, job: str, space_ids: frozenset[uuid.UUID]) -> Principal:
    return Principal(kind="system", user_id=user_id, actor_id=job, space_ids=space_ids)
