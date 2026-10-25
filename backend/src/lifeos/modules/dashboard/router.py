"""Daily briefing, computed locally (no LLM): works when the cloud is unavailable."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from lifeos.api.deps import CtxDep
from lifeos.core.security import utcnow
from lifeos.modules.finance.schemas import BudgetSnapshot
from lifeos.modules.finance.service import budget_snapshot
from lifeos.modules.guardian import gateway
from lifeos.modules.guardian.models import Approval
from lifeos.modules.identity.models import Space
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.journal.schemas import JournalEntryOut
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.notifications.models import Notification
from lifeos.modules.tasks import service as task_service
from lifeos.modules.tasks.models import Task
from lifeos.modules.tasks.schemas import TaskOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
OPEN = ("todo", "in_progress")


class MemoryStats(BaseModel):
    facts: int
    memories: int
    protected: int


class AssistantStatus(BaseModel):
    provider: str
    enabled: bool
    paused: bool
    billing: str
    cloud_enabled_spaces: list[uuid.UUID]
    month_tokens_used: int
    month_token_budget: int
    requests_today: dict[str, int]
    daily_request_caps: dict[str, int]
    cost_month_usd: str


class Dashboard(BaseModel):
    generated_at: datetime
    timezone: str
    today: date
    tasks_overdue: list[TaskOut]
    tasks_today: list[TaskOut]
    tasks_upcoming: list[TaskOut]
    tasks_without_due: int
    journal_today: list[JournalEntryOut]
    pending_approvals: int
    unread_notifications: int
    memory: MemoryStats
    assistant: AssistantStatus
    # Current month budget of the selected space (personal space by default); None while
    # that space has no finance data. Computed locally, like everything here.
    budget: BudgetSnapshot | None = None


@router.get("")
async def dashboard(ctx: CtxDep, space_id: uuid.UUID | None = None) -> Dashboard:
    spaces = ctx.principal.spaces_or_all(space_id)
    uid = ctx.principal.user_id
    now = utcnow()
    tz = ZoneInfo(ctx.user.timezone)
    today = now.astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    tomorrow = start + timedelta(days=1)
    week = start + timedelta(days=8)

    base = select(Task).where(Task.user_id == uid, Task.space_id.in_(spaces), Task.status.in_(OPEN))

    async def tasks(stmt: object) -> list[TaskOut]:
        rows = await ctx.db.scalars(stmt)  # type: ignore[call-overload]
        # With what each one waits for (one query per bucket).
        return await task_service.task_outs(ctx, list(rows))

    overdue = await tasks(base.where(Task.due_at < start).order_by(Task.due_at).limit(50))
    due_today = await tasks(
        base.where(Task.due_at >= start, Task.due_at < tomorrow).order_by(Task.due_at)
    )
    upcoming = await tasks(
        base.where(Task.due_at >= tomorrow, Task.due_at < week).order_by(Task.due_at).limit(20)
    )
    without_due = await ctx.db.scalar(
        select(func.count()).select_from(base.where(Task.due_at.is_(None)).subquery())
    )
    journal = await ctx.db.scalars(
        select(JournalEntry).where(
            JournalEntry.user_id == uid,
            JournalEntry.space_id.in_(spaces),
            JournalEntry.entry_date == today,
        )
    )
    approvals = await ctx.db.scalar(
        select(func.count()).where(
            Approval.user_id == uid, Approval.space_id.in_(spaces), Approval.status == "pending"
        )
    )
    unread = await ctx.db.scalar(
        select(func.count()).where(
            Notification.user_id == uid,
            Notification.status == "pending",
            Notification.deliver_after <= now,
        )
    )
    facts = await ctx.db.scalar(
        select(func.count()).where(
            Fact.user_id == uid, Fact.space_id.in_(spaces), Fact.status == "active"
        )
    )
    memory_counts = (
        await ctx.db.execute(
            select(func.count(), func.count().filter(Memory.protected.is_(True))).where(
                Memory.user_id == uid, Memory.space_id.in_(spaces)
            )
        )
    ).one()
    cloud_spaces = await ctx.db.scalars(
        select(Space.id).where(Space.id.in_(spaces), Space.cloud_ai_enabled.is_(True))
    )
    budget_space = space_id or await ctx.db.scalar(
        select(Space.id).where(Space.user_id == uid, Space.id.in_(spaces), Space.kind == "personal")
    )
    return Dashboard(
        generated_at=now,
        timezone=ctx.user.timezone,
        today=today,
        tasks_overdue=overdue,
        tasks_today=due_today,
        tasks_upcoming=upcoming,
        tasks_without_due=int(without_due or 0),
        journal_today=[JournalEntryOut.model_validate(j) for j in journal],
        pending_approvals=int(approvals or 0),
        unread_notifications=int(unread or 0),
        memory=MemoryStats(
            facts=int(facts or 0), memories=int(memory_counts[0]), protected=int(memory_counts[1])
        ),
        assistant=AssistantStatus(
            **{
                k: v
                for k, v in (await gateway.usage_summary(ctx)).items()
                if k in AssistantStatus.model_fields
            },
            cloud_enabled_spaces=list(cloud_spaces),
        ),
        budget=await budget_snapshot(ctx, budget_space) if budget_space else None,
    )
