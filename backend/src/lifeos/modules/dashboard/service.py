"""Home page, computed locally (no LLM): it works when the cloud is unavailable (task 23).

Everything is read from the database of this server in a bounded number of queries (no
query per item): tasks, synced calendar occurrences, goal milestones, scheduled payments,
stored reviews and notifications. The selected space shows its own titles; the time taken
in the owner's other spaces only shows as « Occupé (autre espace) ».
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, get_args
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on, insert

from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.calendar.service import owner_zone
from lifeos.modules.connectors.models import ConnectorSource
from lifeos.modules.dashboard.models import DashboardPreference
from lifeos.modules.dashboard.schemas import (
    AgendaItem,
    AssistantStatus,
    CardId,
    CardSource,
    Dashboard,
    DashboardLayout,
    DaySummary,
    LayoutCard,
    LayoutIn,
    MemoryStats,
    SuggestionItem,
    WeekOut,
)
from lifeos.modules.finance import projection as engine
from lifeos.modules.finance.models import FinanceRecurring
from lifeos.modules.finance.schemas import BudgetSnapshot
from lifeos.modules.finance.service import budget_snapshot
from lifeos.modules.finance.tracking import as_rule
from lifeos.modules.goals.models import Goal, GoalMilestone
from lifeos.modules.guardian import gateway
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval
from lifeos.modules.identity.models import Space
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.journal.schemas import JournalEntryOut
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.notifications.models import Notification
from lifeos.modules.notifications.service import due
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks import service as task_service
from lifeos.modules.tasks.models import Task
from lifeos.modules.tasks.schemas import TaskOut

OPEN = ("todo", "in_progress")
CARD_IDS: tuple[str, ...] = get_args(CardId)
# Default order: what needs acting on first, then the context.
DEFAULT_CARDS: tuple[str, ...] = CARD_IDS
OTHER_SPACE = "Occupé (autre espace)"
# Bounds that keep the page fast whatever the data (120 s refresh).
MAX_OVERDUE = 50
MAX_UPCOMING = 20
MAX_WINDOW_TASKS = 300
MAX_EVENTS = 500
MAX_MILESTONES = 200
MAX_RECURRING = 500
MAX_SUGGESTIONS = 8
PER_REVIEW = 3
# A review older than this no longer suggests anything on the home page.
REVIEW_FRESHNESS = timedelta(days=35)
_PERIODS = {"daily": "quotidien", "weekly": "hebdomadaire", "monthly": "mensuel"}
_RESPONSES = {"needsAction": "réponse en attente", "tentative": "peut-être", "declined": "refusé"}
_TASK_SOURCES = {"journal_entry": "Journal", "message": "Assistant", "conversation": "Assistant"}


# --- Layout ------------------------------------------------------------------------------------


def merge_cards(stored: Iterable[dict[str, Any]]) -> list[LayoutCard]:
    """Stored order first (unknown or repeated ids dropped), then every card it does not
    mention, visible, in the default order."""
    cards: list[LayoutCard] = []
    seen: set[str] = set()
    for entry in stored:
        card_id = entry.get("id") if isinstance(entry, dict) else None
        if card_id in CARD_IDS and card_id not in seen:
            seen.add(card_id)
            cards.append(LayoutCard(id=card_id, visible=bool(entry.get("visible", True))))
    cards.extend(LayoutCard(id=c, visible=True) for c in DEFAULT_CARDS if c not in seen)
    return cards


def _layout_out(row: DashboardPreference | None) -> DashboardLayout:
    if row is None:
        return DashboardLayout(cards=merge_cards([]))
    return DashboardLayout(
        cards=merge_cards(row.cards or []),
        default_view=row.default_view,
        week_start=row.week_start,
        updated_at=row.updated_at,
    )


async def get_layout(ctx: Ctx) -> DashboardLayout:
    return _layout_out(await ctx.db.get(DashboardPreference, ctx.principal.user_id))


def _invalid(field: str, reason: str) -> ProblemError:
    return ProblemError(
        422,
        "dashboard_layout_invalid",
        "Invalid dashboard layout",
        extra={"field": field, "reason": reason},
    )


async def put_layout(ctx: Ctx, body: LayoutIn) -> DashboardLayout:
    seen: set[str] = set()
    for index, card in enumerate(body.cards):
        if card.id not in CARD_IDS:
            raise _invalid(f"cards[{index}].id", "unknown")
        if card.id in seen:
            raise _invalid(f"cards[{index}].id", "duplicate")
        seen.add(card.id)
    cards = [c.model_dump() for c in merge_cards(c.model_dump() for c in body.cards)]
    now = utcnow()
    values = {
        "cards": cards,
        "default_view": body.default_view,
        "week_start": body.week_start,
        "updated_at": now,
    }
    await ctx.db.execute(
        insert(DashboardPreference)
        .values(user_id=ctx.principal.user_id, **values)
        .on_conflict_do_update(index_elements=["user_id"], set_=values)
    )
    await audit.record(
        ctx.db,
        action="dashboard.layout.update",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        details={
            "cards": len(cards),
            "hidden": sum(1 for c in cards if not c["visible"]),
            "default_view": body.default_view,
            "week_start": body.week_start,
        },
    )
    return DashboardLayout(
        cards=[LayoutCard(**c) for c in cards],
        default_view=body.default_view,
        week_start=body.week_start,
        updated_at=now,
    )


# --- Agenda ------------------------------------------------------------------------------------


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def week_first_day(today: date, week_start: str) -> date:
    """Monday of the current week, or today."""
    return today - timedelta(days=today.weekday()) if week_start == "monday" else today


def _money(amount: Decimal, currency: str) -> str:
    text = f"{amount:,.2f}".replace(",", "\u202f").replace(".", ",")
    return f"{text} €" if currency == "EUR" else f"{text} {currency}"


def _event_days(event: CalendarEvent, zone: ZoneInfo) -> list[date]:
    """Local days an occurrence covers (an end at midnight does not touch the next day)."""
    if event.start_date is not None and event.end_date is not None:
        first, last = event.start_date, event.end_date - timedelta(days=1)
    elif event.starts_at is not None and event.ends_at is not None:
        begin, finish = event.starts_at.astimezone(zone), event.ends_at.astimezone(zone)
        first, last = begin.date(), finish.date()
        if last > first and finish.time() == time.min:
            last -= timedelta(days=1)
    else:
        return []
    return [first + timedelta(days=i) for i in range(max(0, (last - first).days) + 1)]


def _event_detail(event: CalendarEvent) -> str | None:
    parts = [event.location] if event.location else []
    if event.response in _RESPONSES:
        parts.append(_RESPONSES[event.response])
    if not event.busy:
        parts.append("disponible")
    return " · ".join(parts) or None


def task_item(task: TaskOut, zone: ZoneInfo) -> AgendaItem:
    assert task.due_at is not None
    due_at = task.due_at.astimezone(zone)
    blocked = task.blocked and task.status != "done"
    detail = None
    if blocked:
        detail = "Attend " + ", ".join(f"« {b.title} »" for b in task.blocked_by[:3])
    return AgendaItem(
        kind="task",
        title=task.title,
        day=due_at.date(),
        start=due_at,
        space_id=task.space_id,
        source_type="task",
        source_id=task.id,
        source_label=_TASK_SOURCES.get(task.source_type, "Tâche"),
        href="/tasks",
        detail=detail,
        blocked=blocked,
    )


async def agenda(
    ctx: Ctx,
    space_id: uuid.UUID | None,
    first: date,
    last: date,
    tasks: Sequence[TaskOut],
) -> WeekOut:
    """Items of each day of [first, last] (local days): occurrences, the given tasks that
    are due in the window, milestones and scheduled payments. Four queries."""
    zone = owner_zone(ctx)
    uid = ctx.principal.user_id
    scope = ctx.principal.spaces_or_all(space_id)
    begin, finish = _midnight(first, zone), _midnight(last + timedelta(days=1), zone)
    days: dict[date, list[AgendaItem]] = defaultdict(list)

    def add(item: AgendaItem) -> None:
        if first <= item.day <= last:
            days[item.day].append(item)

    for task in tasks:
        if task.due_at is not None and begin <= task.due_at < finish:
            add(task_item(task, zone))

    # Occurrences of every space block time; only those in scope show what they are.
    result = await ctx.db.execute(
        select(CalendarEvent, ConnectorSource.name, ConnectorSource.last_synced_at)
        .join(ConnectorSource, ConnectorSource.id == CalendarEvent.source_id)
        .where(
            CalendarEvent.user_id == uid,
            CalendarEvent.space_id.in_(ctx.principal.space_ids),
            or_(
                and_(
                    CalendarEvent.starts_at < finish.astimezone(UTC),
                    CalendarEvent.ends_at > begin.astimezone(UTC),
                ),
                and_(CalendarEvent.start_date <= last, CalendarEvent.end_date > first),
                and_(
                    CalendarEvent.starts_at == begin.astimezone(UTC),
                    CalendarEvent.ends_at == begin.astimezone(UTC),
                ),
            ),
        )
        .order_by(CalendarEvent.starts_at, CalendarEvent.start_date, CalendarEvent.id)
        .limit(MAX_EVENTS)
    )
    for event, name, synced_at in result.all():
        mine = event.space_id in scope
        if not mine and not event.busy:
            continue  # free time elsewhere: nothing to show
        for day in _event_days(event, zone):
            common: dict[str, Any] = {
                "day": day,
                "start": event.starts_at.astimezone(zone) if event.starts_at else None,
                "end": event.ends_at.astimezone(zone) if event.ends_at else None,
                "all_day": event.start_date is not None,
                "tentative": event.status == "tentative",
            }
            if mine:
                add(
                    AgendaItem(
                        kind="event",
                        title=event.summary or "(sans titre)",
                        space_id=event.space_id,
                        source_type="calendar_event",
                        source_id=event.id,
                        source_label=name,
                        href="/agenda",
                        synced_at=synced_at,
                        detail=_event_detail(event),
                        **common,
                    )
                )
            else:
                add(AgendaItem(kind="busy", title=OTHER_SPACE, **common))

    milestones = await ctx.db.execute(
        select(GoalMilestone, Goal.title, Goal.space_id)
        .join(Goal, Goal.id == GoalMilestone.goal_id)
        .where(
            Goal.user_id == uid,
            Goal.space_id.in_(scope),
            Goal.status.in_(("active", "paused")),
            GoalMilestone.done_at.is_(None),
            GoalMilestone.due_date >= first,
            GoalMilestone.due_date <= last,
        )
        .order_by(GoalMilestone.due_date, GoalMilestone.position)
        .limit(MAX_MILESTONES)
    )
    for milestone, goal_title, goal_space in milestones.all():
        assert milestone.due_date is not None
        add(
            AgendaItem(
                kind="milestone",
                title=milestone.title,
                day=milestone.due_date,
                all_day=True,
                space_id=goal_space,
                source_type="goal",
                source_id=milestone.goal_id,
                source_label=goal_title,
                href=f"/goals?id={milestone.goal_id}",
            )
        )

    readable = [s for s in scope if ctx.principal.can(s, "finances", "read")]
    if readable:
        recurring = await ctx.db.scalars(
            select(FinanceRecurring)
            .where(
                FinanceRecurring.user_id == uid,
                FinanceRecurring.space_id.in_(readable),
                FinanceRecurring.active.is_(True),
                FinanceRecurring.start_date <= last,
                or_(FinanceRecurring.end_date.is_(None), FinanceRecurring.end_date >= first),
            )
            .limit(MAX_RECURRING)
        )
        for rule in recurring:
            sign = "-" if rule.direction == "expense" else "+"
            sure = "estimé" if rule.amount_is_estimate else "prévu"
            for day in engine.occurrences(as_rule(rule), first, last):
                add(
                    AgendaItem(
                        kind="payment",
                        title=rule.label,
                        day=day,
                        all_day=True,
                        space_id=rule.space_id,
                        source_type="finance_recurring",
                        source_id=rule.id,
                        source_label="Opération récurrente",
                        href="/finances",
                        detail=f"{sign}{_money(rule.amount, rule.currency)} · {sure}",
                    )
                )

    calendars, synced = await calendar_freshness(ctx)
    count = (last - first).days + 1
    return WeekOut(
        start=first,
        end=last,
        timezone=str(zone),
        days=[
            DaySummary(date=day, items=sorted(days.get(day, []), key=_order))
            for day in (first + timedelta(days=i) for i in range(count))
        ],
        calendars=calendars,
        synced_at=synced,
    )


def _order(item: AgendaItem) -> tuple[bool, datetime, int, str]:
    kinds = ("milestone", "payment", "event", "busy", "task")
    start = item.start.astimezone(UTC) if item.start else datetime.min.replace(tzinfo=UTC)
    return (not item.all_day, start, kinds.index(item.kind), item.title)


async def calendar_freshness(ctx: Ctx) -> tuple[int, datetime | None]:
    """Followed calendars of the principal's spaces, and the oldest last read."""
    count, oldest = (
        await ctx.db.execute(
            select(func.count(), func.min(ConnectorSource.last_synced_at)).where(
                ConnectorSource.user_id == ctx.principal.user_id,
                ConnectorSource.space_id.in_(ctx.principal.space_ids),
                ConnectorSource.kind == "calendar",
                ConnectorSource.enabled.is_(True),
            )
        )
    ).one()
    return int(count), oldest


def _open_tasks(ctx: Ctx, spaces: frozenset[uuid.UUID]) -> Any:
    return select(Task).where(
        Task.user_id == ctx.principal.user_id, Task.space_id.in_(spaces), Task.status.in_(OPEN)
    )


async def week(ctx: Ctx, space_id: uuid.UUID | None, start: date | None) -> WeekOut:
    """Seven local days from `start` (default: this week, as the layout says)."""
    spaces = ctx.principal.spaces_or_all(space_id)
    zone = owner_zone(ctx)
    if start is None:
        layout = await get_layout(ctx)
        start = week_first_day(utcnow().astimezone(zone).date(), layout.week_start)
    if not date(1900, 1, 1) <= start <= date(2200, 12, 31):
        raise ProblemError(422, "invalid_window", "The week must start between 1900 and 2200")
    last = start + timedelta(days=6)
    rows = list(
        await ctx.db.scalars(
            _open_tasks(ctx, spaces)
            .where(
                Task.due_at >= _midnight(start, zone),
                Task.due_at < _midnight(last + timedelta(days=1), zone),
            )
            .order_by(Task.due_at)
            .limit(MAX_WINDOW_TASKS)
        )
    )
    return await agenda(ctx, space_id, start, last, await task_service.task_outs(ctx, rows))


# --- Suggestions -------------------------------------------------------------------------------


def _source_href(source_type: str | None, source_id: uuid.UUID | None) -> str:
    if source_type == "review" and source_id:
        return f"/reviews?review={source_id}"
    if source_type == "goal" and source_id:
        return f"/goals?id={source_id}"
    if source_type == "document" and source_id:
        return f"/documents?id={source_id}"
    return {"task": "/tasks", "journal_entry": "/journal", "calendar_event": "/agenda"}.get(
        source_type or "", "/"
    )


async def suggestions(
    ctx: Ctx, spaces: frozenset[uuid.UUID], budget: BudgetSnapshot | None, now: datetime
) -> tuple[list[SuggestionItem], datetime | None]:
    """Stored suggestions only: those of the latest review of each space (35 days at
    most), the grouped suggestion notifications not read yet, then the budget alerts.
    Returns them with the generation time of the latest review used."""
    uid = ctx.principal.user_id
    reviews = list(
        await ctx.db.scalars(
            select(Review)
            .where(
                Review.user_id == uid,
                Review.space_id.in_(spaces),
                Review.narrative_status == "generated",
                Review.generated_at >= now - REVIEW_FRESHNESS,
            )
            .ext(distinct_on(Review.space_id))
            .order_by(Review.space_id, Review.generated_at.desc())
        )
    )
    reviews.sort(key=lambda r: r.generated_at, reverse=True)
    items: list[SuggestionItem] = []
    for review in reviews:
        period = _PERIODS.get(review.period, review.period)
        label = f"Bilan {period} du {review.period_start:%d/%m/%Y}"
        raw = (review.narrative or {}).get("suggestions") or []
        for entry in raw[:PER_REVIEW]:
            if not isinstance(entry, dict) or not entry.get("action"):
                continue
            items.append(
                SuggestionItem(
                    text=str(entry["action"]),
                    why=str(entry["why"]) if entry.get("why") else None,
                    source_type="review",
                    source_id=review.id,
                    source_label=label,
                    date=review.generated_at,
                    href=f"/reviews?review={review.id}",
                    space_id=review.space_id,
                )
            )
    shown = {r.id for r in reviews}
    grouped = await ctx.db.scalars(
        select(Notification)
        .where(
            Notification.user_id == uid,
            Notification.category == "suggestion",
            Notification.status == "pending",
            Notification.digest_id.is_not(None),
            Notification.deliver_after <= now,
            or_(Notification.space_id.is_(None), Notification.space_id.in_(spaces)),
        )
        .order_by(Notification.deliver_after.desc())
        .limit(MAX_SUGGESTIONS)
    )
    for note in grouped:
        if note.source_type == "review" and note.source_id in shown:
            continue  # its suggestions are already listed
        items.append(
            SuggestionItem(
                text=note.title,
                why=note.body,
                source_type="notification",
                source_id=note.id,
                source_label="Synthèse des suggestions",
                date=note.deliver_after,
                href=_source_href(note.source_type, note.source_id),
                space_id=note.space_id,
            )
        )
    if budget is not None:
        for alert in budget.alerts:
            items.append(
                SuggestionItem(
                    text=f"Surveiller « {alert.name} » : {round(alert.ratio * 100)} % du budget",
                    why=(
                        f"{_money(alert.spent, budget.currency)} dépensés sur "
                        f"{_money(alert.budget, budget.currency)} budgétés ce mois-ci"
                    ),
                    source_type="budget",
                    source_id=alert.category_id,
                    source_label=f"Budget du mois {budget.month}",
                    date=now,
                    href="/finances",
                    space_id=budget.space_id,
                )
            )
    latest = reviews[0].generated_at if reviews else None
    return items[:MAX_SUGGESTIONS], latest


# --- Dashboard ---------------------------------------------------------------------------------


async def dashboard(ctx: Ctx, space_id: uuid.UUID | None) -> Dashboard:
    spaces = ctx.principal.spaces_or_all(space_id)
    uid = ctx.principal.user_id
    now = utcnow()
    zone = owner_zone(ctx)
    today = now.astimezone(zone).date()
    start = _midnight(today, zone)
    tomorrow = _midnight(today + timedelta(days=1), zone)
    horizon = _midnight(today + timedelta(days=8), zone)
    layout = await get_layout(ctx)
    first = week_first_day(today, layout.week_start)
    last = first + timedelta(days=6)
    window_start = min(_midnight(first, zone), start)
    window_end = max(_midnight(last + timedelta(days=1), zone), horizon)

    # Two task queries and one for what they wait for, whatever the number of buckets.
    base = _open_tasks(ctx, spaces)
    overdue_rows = list(
        await ctx.db.scalars(
            base.where(Task.due_at < start).order_by(Task.due_at).limit(MAX_OVERDUE)
        )
    )
    window_rows = list(
        await ctx.db.scalars(
            base.where(Task.due_at >= window_start, Task.due_at < window_end)
            .order_by(Task.due_at)
            .limit(MAX_WINDOW_TASKS)
        )
    )
    unique = {t.id: t for t in [*overdue_rows, *window_rows]}
    outs = {t.id: t for t in await task_service.task_outs(ctx, list(unique.values()))}
    overdue = [outs[t.id] for t in overdue_rows]
    window = [outs[t.id] for t in window_rows]
    due_today = [t for t in window if t.due_at and start <= t.due_at < tomorrow]
    upcoming = [t for t in window if t.due_at and tomorrow <= t.due_at < horizon][:MAX_UPCOMING]

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
    # As the notification menu counts them, within the selected space (or none).
    unread = await ctx.db.scalar(
        select(func.count()).where(
            Notification.user_id == uid,
            Notification.status == "pending",
            or_(Notification.space_id.is_(None), Notification.space_id.in_(spaces)),
            *due(now),
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
    budget = await budget_snapshot(ctx, budget_space) if budget_space else None
    week_out = await agenda(ctx, space_id, first, last, window)
    agenda_today = next((d.items for d in week_out.days if d.date == today), [])
    found, review_at = await suggestions(ctx, spaces, budget, now)

    local = CardSource(origin="local", as_of=now)
    calendar = (
        CardSource(origin="calendar", as_of=week_out.synced_at, count=week_out.calendars)
        if week_out.calendars
        else CardSource(origin="local", as_of=now, count=0)
    )
    return Dashboard(
        generated_at=now,
        timezone=str(zone),
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
        budget=budget,
        agenda_today=agenda_today,
        week=week_out,
        suggestions=found,
        sources={
            "priorities": local.model_copy(update={"count": len(overdue) + len(due_today)}),
            "agenda": calendar,
            "week": calendar,
            "suggestions": (
                CardSource(origin="review", as_of=review_at, count=len(found))
                if review_at
                else local.model_copy(update={"count": len(found)})
            ),
            "journal": local,
            "approvals": local,
            "memory": local,
            "budget": local,
            "assistant": local,
        },
        layout=layout,
    )
