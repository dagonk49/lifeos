import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from lifeos.api.deps import CtxDep
from lifeos.core.errors import not_found
from lifeos.modules.agents.registry import AGENT_SPECS
from lifeos.modules.guardian.permissions import set_bundle
from lifeos.modules.guardian.principal import agent_principal

router = APIRouter(prefix="/agents", tags=["agents"])
ACCESS_DAYS = 90


class AgentOut(BaseModel):
    name: str
    label: str
    description: str
    resources: list[str]
    # Resources the agent cannot do without, still to be granted in this space.
    missing_permissions: list[str]
    # Optional resources (e.g. receipts for the finance agent) not granted: the agent
    # works without them, with less context.
    missing_optional: list[str] = []


class AccessUpdate(BaseModel):
    space_id: uuid.UUID
    enabled: bool


async def _describe(ctx: CtxDep, space_id: uuid.UUID) -> list[AgentOut]:
    ctx.principal.require_space(space_id)
    result = []
    for spec in AGENT_SPECS.values():
        agent = await agent_principal(ctx.db, ctx.principal.user_id, spec.name)
        missing = [r for r in spec.resources if not agent.can(space_id, r, "read")]
        result.append(
            AgentOut(
                name=spec.name,
                label=spec.label,
                description=spec.description,
                resources=list(spec.resources),
                missing_permissions=[r for r in missing if r not in spec.optional_resources],
                missing_optional=[r for r in missing if r in spec.optional_resources],
            )
        )
    return result


@router.get("")
async def list_agents(space_id: uuid.UUID, ctx: CtxDep) -> list[AgentOut]:
    return await _describe(ctx, space_id)


@router.put("/{name}/access")
async def set_access(name: str, body: AccessUpdate, ctx: CtxDep) -> list[AgentOut]:
    """Read access for the agent's resources in one space, 90 days (reinforced to grant)."""
    spec = AGENT_SPECS.get(name)
    if spec is None:
        raise not_found("agent_not_found", "Agent not found")
    await set_bundle(
        ctx,
        agent=spec.name,
        space_id=body.space_id,
        resources=spec.resources,
        purpose=f"Assistant {spec.label}",
        enabled=body.enabled,
        days=ACCESS_DAYS,
    )
    return await _describe(ctx, body.space_id)
