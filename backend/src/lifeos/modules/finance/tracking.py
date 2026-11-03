"""Recurring items and projections (F04, task 26).

Recurring expenses and incomes are declared by the owner (never inferred and recorded on
their own). The projection is computed on every read from the operations, the recurring
items and the savings goals as they are now: a correction anywhere shows at once, and
nothing is stored. Everything is local SQL plus Python, never the LLM.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from lifeos.core.errors import ProblemError, not_found
from lifeos.modules.finance import projection as engine
from lifeos.modules.finance.models import FinanceCategory, FinanceRecurring, Transaction
from lifeos.modules.finance.schemas import (
    FinanceProjection,
    RecurringCreate,
    RecurringOut,
    RecurringUpdate,
)
from lifeos.modules.finance.service import _audit, _category_for, _q, _readable, local_today
from lifeos.modules.goals.models import Goal, GoalCheckin
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.privacy import erasure

_ZERO = Decimal("0.00")


# --- Recurring items -------------------------------------------------------------------------


def as_rule(item: FinanceRecurring) -> engine.Recurring:
    return engine.Recurring(
        id=item.id,
        label=item.label,
        direction=item.direction,
        amount=item.amount,
        cadence=item.cadence,
        start_date=item.start_date,
        end_date=item.end_date,
        is_estimate=item.amount_is_estimate,
        low=item.amount_low,
        high=item.amount_high,
        category_id=item.category_id,
        active=item.active,
    )


def recurring_out(ctx: Ctx, item: FinanceRecurring) -> RecurringOut:
    out = RecurringOut.model_validate(item)
    out.next_date = engine.next_occurrence(as_rule(item), local_today(ctx))
    return out


async def get_recurring(ctx: Ctx, recurring_id: uuid.UUID) -> FinanceRecurring:
    item = await ctx.db.scalar(
        select(FinanceRecurring).where(
            FinanceRecurring.id == recurring_id,
            FinanceRecurring.user_id == ctx.principal.user_id,
            FinanceRecurring.space_id.in_(_readable(ctx, None)),
        )
    )
    if item is None:
        raise not_found("recurring_not_found", "Recurring item not found")
    return item


async def list_recurring(
    ctx: Ctx, *, space_id: uuid.UUID | None, include_inactive: bool = False
) -> list[FinanceRecurring]:
    spaces = _readable(ctx, space_id)
    stmt = select(FinanceRecurring).where(
        FinanceRecurring.user_id == ctx.principal.user_id, FinanceRecurring.space_id.in_(spaces)
    )
    if not include_inactive:
        stmt = stmt.where(FinanceRecurring.active.is_(True))
    rows = await ctx.db.scalars(
        stmt.order_by(FinanceRecurring.direction.desc(), FinanceRecurring.label).limit(500)
    )
    return list(rows)


def _check(item: FinanceRecurring) -> None:
    if item.end_date is not None and item.end_date < item.start_date:
        raise ProblemError(422, "recurring_dates_invalid", "The end date is before the start date")
    if not item.amount_is_estimate and (item.amount_low or item.amount_high):
        raise ProblemError(
            422, "recurring_range_invalid", "A range is only for a variable (estimated) amount"
        )
    if (item.amount_low is not None and item.amount_low > item.amount) or (
        item.amount_high is not None and item.amount_high < item.amount
    ):
        raise ProblemError(
            422, "recurring_range_invalid", "The amount must lie between the low and high values"
        )


def _money(value: Decimal | None) -> Decimal | None:
    return _q(value) if value is not None else None


async def create_recurring(ctx: Ctx, data: RecurringCreate) -> FinanceRecurring:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "finance.write", data.space_id)
    if data.category_id is not None:
        await _category_for(ctx, data.category_id, data.space_id, data.direction)
    item = FinanceRecurring(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        label=data.label,
        merchant=data.merchant,
        amount=_q(data.amount),
        amount_is_estimate=data.amount_is_estimate,
        amount_low=_money(data.amount_low),
        amount_high=_money(data.amount_high),
        direction=data.direction,
        currency=data.currency,
        category_id=data.category_id,
        cadence=data.cadence,
        start_date=data.start_date,
        end_date=data.end_date,
    )
    _check(item)
    ctx.db.add(item)
    await ctx.db.flush()
    await _audit(
        ctx,
        "finance.recurring.create",
        item.space_id,
        "finance_recurring",
        item.id,
        cadence=item.cadence,
        estimated=item.amount_is_estimate,
    )
    return item


async def update_recurring(
    ctx: Ctx, recurring_id: uuid.UUID, data: RecurringUpdate, *, if_match: int | None = None
) -> FinanceRecurring:
    item = await get_recurring(ctx, recurring_id)
    await authorize(ctx, "finance.write", item.space_id)
    if if_match is not None and if_match != item.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The recurring item was modified elsewhere",
            extra={"current_version": item.version},
        )
    changes = data.model_dump(exclude_unset=True)
    required = (
        "label",
        "amount",
        "amount_is_estimate",
        "direction",
        "currency",
        "cadence",
        "start_date",
        "active",
    )
    for key in required:
        if key in changes and changes[key] is None:
            changes.pop(key)
    direction = changes.get("direction", item.direction)
    category_id = changes.get("category_id", item.category_id)
    if category_id is not None and ("category_id" in changes or "direction" in changes):
        await _category_for(ctx, category_id, item.space_id, direction)
    if changes.get("amount_is_estimate") is False:
        changes.setdefault("amount_low", None)
        changes.setdefault("amount_high", None)
    for key, value in changes.items():
        if key in ("amount", "amount_low", "amount_high"):
            value = _money(value)
        setattr(item, key, value)
    _check(item)
    await ctx.db.flush()
    await _audit(
        ctx,
        "finance.recurring.update",
        item.space_id,
        "finance_recurring",
        item.id,
        fields=",".join(sorted(changes)),
    )
    return item


async def delete_recurring(ctx: Ctx, recurring_id: uuid.UUID) -> None:
    """Its operations stay, unlinked."""
    item = await get_recurring(ctx, recurring_id)
    await authorize(ctx, "finance.write", item.space_id)
    erased = await erasure.erase(ctx.db, item)
    await _audit(
        ctx, "finance.recurring.delete", item.space_id, "finance_recurring", item.id, **erased
    )


# --- Projection --------------------------------------------------------------------------------


async def projection(
    ctx: Ctx,
    *,
    space_id: uuid.UUID,
    months: int = 6,
    baseline_months: int = 3,
    currency: str = "EUR",
) -> FinanceProjection:
    """Current month plus `months` months, recomputed from the current data on every read."""
    _readable(ctx, space_id)
    uid = ctx.principal.user_id
    today = local_today(ctx)
    current = engine.month_start(today)
    current_end = engine.month_end(current)
    of_space = (Transaction.user_id == uid, Transaction.space_id == space_id)
    in_currency = (*of_space, Transaction.currency == currency)

    first_day = await ctx.db.scalar(select(func.min(Transaction.tx_date)).where(*in_currency))
    window = engine.baseline_window(today, baseline_months, first_day)
    history: dict[tuple[str, str, uuid.UUID | None], Decimal] = {}
    if window is not None:
        start, last, _ = window
        month = func.to_char(Transaction.tx_date, "YYYY-MM")
        for key, direction, category, total in await ctx.db.execute(
            select(
                month, Transaction.direction, Transaction.category_id, func.sum(Transaction.amount)
            )
            .where(
                *in_currency,
                Transaction.recurring_id.is_(None),
                Transaction.tx_date >= start,
                Transaction.tx_date <= engine.month_end(last),
            )
            .group_by(month, Transaction.direction, Transaction.category_id)
        ):
            history[(key, direction, category)] = Decimal(total)
    this_month = (*in_currency, Transaction.tx_date >= current, Transaction.tx_date <= current_end)
    current_known = {
        direction: Decimal(total)
        for direction, total in await ctx.db.execute(
            select(Transaction.direction, func.sum(Transaction.amount))
            .where(*this_month)
            .group_by(Transaction.direction)
        )
    }
    current_spent = {
        (direction, category): Decimal(total)
        for direction, category, total in await ctx.db.execute(
            select(Transaction.direction, Transaction.category_id, func.sum(Transaction.amount))
            .where(*this_month, Transaction.recurring_id.is_(None))
            .group_by(Transaction.direction, Transaction.category_id)
        )
    }
    realised = {
        recurring_id: int(count)
        for recurring_id, count in await ctx.db.execute(
            select(Transaction.recurring_id, func.count())
            .where(*this_month, Transaction.recurring_id.is_not(None))
            .group_by(Transaction.recurring_id)
        )
        if recurring_id is not None
    }
    since = window[0] if window else current
    other_currency = await ctx.db.scalar(
        select(func.count()).where(
            *of_space, Transaction.currency != currency, Transaction.tx_date >= since
        )
    )
    items = await list_recurring(ctx, space_id=space_id)
    rules = [as_rule(i) for i in items if i.currency == currency]
    names = {
        category_id: name
        for category_id, name in await ctx.db.execute(
            select(FinanceCategory.id, FinanceCategory.name).where(
                FinanceCategory.user_id == uid, FinanceCategory.space_id == space_id
            )
        )
    }
    goals = None
    if ctx.principal.can(space_id, "goals", "read"):
        goals = await _savings_goals(ctx, space_id, currency)
    return engine.project(
        space_id=space_id,
        currency=currency,
        today=today,
        horizon=months,
        baseline_months=baseline_months,
        first_month=first_day,
        history=history,
        current_known=current_known,
        current_spent=current_spent,
        realised=realised,
        recurring=rules,
        category_names=names,
        goals=goals,
        other_currency=int(other_currency or 0),
        other_currency_recurring=sum(1 for i in items if i.currency != currency),
    )


async def _savings_goals(ctx: Ctx, space_id: uuid.UUID, currency: str) -> list[engine.SavingsGoal]:
    goals = list(
        await ctx.db.scalars(
            select(Goal)
            .where(
                Goal.user_id == ctx.principal.user_id,
                Goal.space_id == space_id,
                Goal.kind == "measurable",
                Goal.status == "active",
                Goal.currency == currency,
            )
            .order_by(Goal.due_date.asc().nulls_last(), Goal.created_at)
        )
    )
    last: dict[uuid.UUID, Any] = {}
    if goals:
        last = {
            goal_id: when
            for goal_id, when in await ctx.db.execute(
                select(GoalCheckin.goal_id, func.max(GoalCheckin.created_at))
                .where(
                    GoalCheckin.goal_id.in_([g.id for g in goals]), GoalCheckin.value.is_not(None)
                )
                .group_by(GoalCheckin.goal_id)
            )
        }
    result = []
    for goal in goals:
        if goal.target_value is None:
            continue
        saved_at: date | None = None
        if goal.id in last:
            saved_at = local_today(ctx, last[goal.id])
        result.append(
            engine.SavingsGoal(
                id=goal.id,
                title=goal.title,
                target=goal.target_value,
                saved=goal.current_value or goal.start_value or _ZERO,
                saved_at=saved_at,
                due_date=goal.due_date,
                contribution=goal.monthly_contribution,
            )
        )
    return result
