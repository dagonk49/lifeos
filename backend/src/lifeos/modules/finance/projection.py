"""Local financial projections (F04, task 26): pure, exact (Decimal), never stored.

Every amount says what it rests on (cahier des charges §9, « prévisions explicitement
incertaines »):

- « connu » (known): operations already recorded this month;
- « prévu » (scheduled): a recurring item declared with a fixed amount;
- « estimé » (estimated): a recurring item with a variable amount, or the average of the
  last complete months for what is not recurring, always with a low-high range.

The method and its limits are returned with the figures (`assumptions`, `warnings`), and
nothing here calls the LLM: the projection works offline (§9).
"""

import calendar
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from lifeos.modules.finance.schemas import (
    FinanceProjection,
    ProjectionBaseline,
    ProjectionItem,
    ProjectionMonth,
    ProjectionNote,
    SavingsGoalProjection,
)

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")
# Below this many complete months, the range is widened by this share of the average.
SHORT_HISTORY = 3
WIDENING = Decimal("0.25")
MONTH_STEPS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


# --- Calendar ----------------------------------------------------------------------------------


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def month_key(month: date) -> str:
    return f"{month:%Y-%m}"


@dataclass(frozen=True)
class Recurring:
    id: uuid.UUID
    label: str
    direction: str
    amount: Decimal
    cadence: str
    start_date: date
    end_date: date | None = None
    is_estimate: bool = False
    low: Decimal | None = None
    high: Decimal | None = None
    category_id: uuid.UUID | None = None
    active: bool = True


def occurrences(rule: Recurring, start: date, end: date) -> list[date]:
    """Dates of `rule` between `start` and `end` (inclusive). A day 31 falls on the last day
    of shorter months (28 February), and the end date is honoured."""
    if not rule.active:
        return []
    last = min(end, rule.end_date) if rule.end_date else end
    first = max(start, rule.start_date)
    if first > last:
        return []
    if rule.cadence == "once":
        return [rule.start_date] if first <= rule.start_date <= last else []
    if rule.cadence == "weekly":
        offset = (first - rule.start_date).days
        current = rule.start_date + timedelta(days=-(-offset // 7) * 7)
        found = []
        while current <= last:
            found.append(current)
            current += timedelta(days=7)
        return found
    step = MONTH_STEPS[rule.cadence]
    origin = month_start(rule.start_date)
    elapsed = (first.year - origin.year) * 12 + first.month - origin.month
    index = max(0, elapsed // step)
    found = []
    while True:
        month = add_months(origin, index * step)
        day = month.replace(day=min(rule.start_date.day, month_end(month).day))
        if day > last:
            return found
        if day >= first:
            found.append(day)
        index += 1


def next_occurrence(rule: Recurring, today: date) -> date | None:
    upcoming = occurrences(rule, today, today + timedelta(days=366 * 2))
    return upcoming[0] if upcoming else None


# --- Baseline ----------------------------------------------------------------------------------


def baseline_window(
    today: date, months: int, first_month: date | None
) -> tuple[date, date, int] | None:
    """(first month, last month, count) of the complete months averaged: the last `months`
    complete months, not before the first recorded operation. None without history."""
    last = add_months(month_start(today), -1)
    start = add_months(last, -(months - 1))
    if first_month is not None and month_start(first_month) > start:
        start = month_start(first_month)
    if first_month is None or start > last:
        return None
    count = (last.year - start.year) * 12 + last.month - start.month + 1
    return start, last, count


@dataclass(frozen=True)
class Baseline:
    mean: Decimal
    low: Decimal
    high: Decimal


def baselines(
    history: Mapping[tuple[str, str, uuid.UUID | None], Decimal], window: tuple[date, date, int]
) -> dict[tuple[str, uuid.UUID | None], Baseline]:
    """Per (direction, category): mean of the window's months (a month without any
    operation counts as zero), range from the lowest to the highest month."""
    start, _, count = window
    keys = [month_key(add_months(start, i)) for i in range(count)]
    by_line: dict[tuple[str, uuid.UUID | None], list[Decimal]] = {}
    for (month, direction, category), amount in history.items():
        if month in keys:
            by_line.setdefault((direction, category), [_ZERO] * count)[keys.index(month)] += amount
    result = {}
    for line, values in by_line.items():
        mean = _q(sum(values, _ZERO) / count)
        if mean <= 0:
            continue
        low, high = min(values), max(values)
        if count < SHORT_HISTORY:
            low = min(low, _q(mean * (1 - WIDENING)))
            high = max(high, _q(mean * (1 + WIDENING)))
        result[line] = Baseline(mean=mean, low=_q(low), high=_q(high))
    return result


# --- Projection --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SavingsGoal:
    id: uuid.UUID
    title: str
    target: Decimal
    saved: Decimal
    saved_at: date | None = None
    due_date: date | None = None
    contribution: Decimal | None = None


@dataclass
class _Totals:
    known: Decimal = _ZERO
    scheduled: Decimal = _ZERO
    estimated: Decimal = _ZERO
    low: Decimal = _ZERO
    high: Decimal = _ZERO


def _label(direction: str, category: uuid.UUID | None, names: Mapping[uuid.UUID, str]) -> str:
    if category is not None and category in names:
        return names[category]
    return "Dépenses non classées" if direction == "expense" else "Revenus non classés"


def project(
    *,
    space_id: uuid.UUID,
    currency: str,
    today: date,
    horizon: int,
    baseline_months: int,
    first_month: date | None,
    history: Mapping[tuple[str, str, uuid.UUID | None], Decimal],
    current_known: Mapping[str, Decimal],
    current_spent: Mapping[tuple[str, uuid.UUID | None], Decimal],
    realised: Mapping[uuid.UUID, int],
    recurring: Sequence[Recurring],
    category_names: Mapping[uuid.UUID, str],
    goals: Sequence[SavingsGoal] | None,
    other_currency: int = 0,
    other_currency_recurring: int = 0,
) -> FinanceProjection:
    """The current month and the next `horizon` months, every amount tagged with its basis.

    `history`: (YYYY-MM, direction, category) -> total of the operations not linked to a
    recurring item, over the baseline window. `current_known`: per direction, everything
    recorded this month. `current_spent`: per (direction, category), this month's operations
    not linked to a recurring item. `realised`: recurring id -> operations linked to it this
    month (those occurrences are known, not scheduled again).
    """
    window = baseline_window(today, baseline_months, first_month)
    lines = baselines(history, window) if window else {}
    current = month_start(today)
    months: list[ProjectionMonth] = []
    cumulative = cumulative_low = cumulative_high = _ZERO
    for offset in range(horizon + 1):
        month = add_months(current, offset)
        is_current = offset == 0
        totals = {"income": _Totals(), "expense": _Totals()}
        items: list[ProjectionItem] = []
        if is_current:
            for direction in ("income", "expense"):
                known = _q(current_known.get(direction, _ZERO))
                totals[direction].known = known
                if known:
                    items.append(
                        ProjectionItem(
                            label="Opérations enregistrées",
                            direction=direction,
                            basis="known",
                            source="transactions",
                            amount=known,
                            low=known,
                            high=known,
                        )
                    )
        for rule in recurring:
            dates = occurrences(rule, month, month_end(month))
            if is_current:
                # Occurrences already paid (linked operations) are known, not scheduled again.
                dates = dates[realised.get(rule.id, 0) :]
            if not dates:
                continue
            count = len(dates)
            amount = _q(rule.amount * count)
            low = _q((rule.low or rule.amount) * count) if rule.is_estimate else amount
            high = _q((rule.high or rule.amount) * count) if rule.is_estimate else amount
            line = totals[rule.direction]
            if rule.is_estimate:
                line.estimated += amount
                line.low += low
                line.high += high
            else:
                line.scheduled += amount
            items.append(
                ProjectionItem(
                    label=rule.label,
                    direction=rule.direction,
                    basis="estimated" if rule.is_estimate else "scheduled",
                    source="recurring",
                    amount=amount,
                    low=low,
                    high=high,
                    occurrences=count,
                    recurring_id=rule.id,
                    category_id=rule.category_id,
                )
            )
        for (direction, category), base in sorted(
            lines.items(),
            key=lambda item: (item[0][0], _label(item[0][0], item[0][1], category_names)),
        ):
            mean, low, high = base.mean, base.low, base.high
            if is_current:
                # What is left of the usual month, given what was already spent.
                spent = current_spent.get((direction, category), _ZERO)
                mean = max(_ZERO, mean - spent)
                low = max(_ZERO, low - spent)
                high = max(_ZERO, high - spent)
            if not (mean or high):
                continue
            line = totals[direction]
            line.estimated += mean
            line.low += low
            line.high += high
            items.append(
                ProjectionItem(
                    label=_label(direction, category, category_names),
                    direction=direction,
                    basis="estimated",
                    source="baseline",
                    amount=_q(mean),
                    low=_q(low),
                    high=_q(high),
                    category_id=category,
                )
            )
        income, expense = totals["income"], totals["expense"]
        sure_income = income.known + income.scheduled
        sure_expense = expense.known + expense.scheduled
        balance = sure_income + income.estimated - sure_expense - expense.estimated
        balance_low = sure_income + income.low - sure_expense - expense.high
        balance_high = sure_income + income.high - sure_expense - expense.low
        cumulative += balance
        cumulative_low += balance_low
        cumulative_high += balance_high
        months.append(
            ProjectionMonth(
                month=month_key(month),
                state="current" if is_current else "future",
                known_income=_q(income.known),
                known_expenses=_q(expense.known),
                scheduled_income=_q(income.scheduled),
                scheduled_expenses=_q(expense.scheduled),
                estimated_income=_q(income.estimated),
                estimated_income_low=_q(income.low),
                estimated_income_high=_q(income.high),
                estimated_expenses=_q(expense.estimated),
                estimated_expenses_low=_q(expense.low),
                estimated_expenses_high=_q(expense.high),
                balance=_q(balance),
                balance_low=_q(balance_low),
                balance_high=_q(balance_high),
                cumulative=_q(cumulative),
                cumulative_low=_q(cumulative_low),
                cumulative_high=_q(cumulative_high),
                items=items,
            )
        )

    warnings: list[ProjectionNote] = []
    if window is None:
        warnings.append(
            ProjectionNote(
                code="no_history",
                label="Aucun mois complet enregistré : seules les échéances déclarées sont "
                "projetées, les autres dépenses ne sont pas estimées.",
            )
        )
    elif window[2] < SHORT_HISTORY:
        warnings.append(
            ProjectionNote(
                code="short_history",
                label=f"Seulement {window[2]} mois complet(s) d'historique : les estimations "
                "sont fragiles et leur fourchette est élargie.",
                value=str(window[2]),
            )
        )
    if other_currency:
        warnings.append(
            ProjectionNote(
                code="other_currency",
                label=f"{other_currency} opération(s) dans une autre devise que {currency}, "
                "non converties ni comptées.",
                value=str(other_currency),
            )
        )
    if other_currency_recurring:
        warnings.append(
            ProjectionNote(
                code="recurring_other_currency",
                label=f"{other_currency_recurring} échéance(s) dans une autre devise, "
                "non comptée(s).",
                value=str(other_currency_recurring),
            )
        )

    savings = None
    if goals is not None:
        savings = [_goal(goal, months, currency, warnings) for goal in goals]
        if sum(1 for s in savings if s.contribution_source == "estimated") > 1:
            warnings.append(
                ProjectionNote(
                    code="shared_balance",
                    label="Plusieurs objectifs sans versement déclaré : chacun est estimé avec "
                    "le même solde mensuel, qui devra en réalité être partagé.",
                )
            )

    return FinanceProjection(
        space_id=space_id,
        currency=currency,
        today=today,
        horizon=horizon,
        baseline=ProjectionBaseline(
            months=window[2] if window else 0,
            start=month_key(window[0]) if window else None,
            end=month_key(window[1]) if window else None,
        ),
        months=months,
        savings_goals=savings,
        assumptions=_assumptions(window, baseline_months, currency),
        warnings=warnings,
    )


def _eta(remaining: Decimal, contribution: Decimal | None, current: date) -> str | None:
    if contribution is None or contribution <= 0:
        return None
    # Contributions start next month.
    return month_key(add_months(current, math.ceil(remaining / contribution)))


def _goal(
    goal: SavingsGoal,
    months: Sequence[ProjectionMonth],
    currency: str,
    warnings: list[ProjectionNote],
) -> SavingsGoalProjection:
    current = date.fromisoformat(f"{months[0].month}-01")
    remaining = max(_ZERO, _q(goal.target - goal.saved))
    reached = remaining <= 0
    future = [m for m in months if m.state == "future"] or list(months)
    source: str
    if goal.contribution is not None and goal.contribution > 0:
        source, contribution = "declared", _q(goal.contribution)
        early = late = _eta(remaining, contribution, current)
    else:
        average = _q(sum((m.balance for m in future), _ZERO) / len(future))
        average_low = _q(sum((m.balance_low for m in future), _ZERO) / len(future))
        average_high = _q(sum((m.balance_high for m in future), _ZERO) / len(future))
        if average > 0:
            source, contribution = "estimated", average
            early = _eta(remaining, average_high, current)
            late = _eta(remaining, average_low, current)
        else:
            source, contribution, early, late = "none", None, None, None
        if not reached:
            warnings.append(
                ProjectionNote(
                    code="no_contribution",
                    label=f"« {goal.title[:80]} » : aucun versement mensuel déclaré, la date "
                    "est estimée à partir du solde prévu (si positif).",
                )
            )
    eta = month_key(current) if reached else _eta(remaining, contribution, current)
    on_track: bool | None = None
    if goal.due_date is not None:
        due = month_key(goal.due_date)
        on_track = True if reached else (eta is not None and eta <= due)
    return SavingsGoalProjection(
        goal_id=goal.id,
        title=goal.title,
        currency=currency,
        target=_q(goal.target),
        saved=_q(goal.saved),
        saved_at=goal.saved_at,
        remaining=remaining,
        reached=reached,
        contribution=contribution,
        contribution_source=source,
        eta_month=eta,
        eta_early=eta if reached else early,
        eta_late=eta if reached else late,
        due_date=goal.due_date,
        on_track=on_track,
    )


def _assumptions(
    window: tuple[date, date, int] | None, requested: int, currency: str
) -> list[ProjectionNote]:
    notes = []
    if window is not None:
        start, end, count = window
        notes.append(
            ProjectionNote(
                code="baseline_window",
                label=f"Dépenses et revenus non récurrents : moyenne des {count} derniers mois "
                f"complets ({month_key(start)} à {month_key(end)}), {requested} demandé(s).",
                value=str(count),
            )
        )
        widened = " (élargie de 25 % : historique court)" if count < SHORT_HISTORY else ""
        notes.append(
            ProjectionNote(
                code="range",
                label="Fourchette : du mois le plus bas au mois le plus haut de cette période"
                + widened
                + ".",
            )
        )
    notes += [
        ProjectionNote(
            code="recurring",
            label="Échéances déclarées : montant fixe = prévu ; montant variable = estimé, "
            "avec sa fourchette.",
        ),
        ProjectionNote(
            code="no_double_count",
            label="Les opérations rattachées à une échéance ne comptent pas dans la moyenne.",
        ),
        ProjectionNote(
            code="current_month",
            label="Mois en cours : opérations enregistrées (connu), plus ce qu'il reste de la "
            "moyenne habituelle (estimé).",
        ),
        ProjectionNote(
            code="currency",
            label=f"Montants en {currency} uniquement, sans conversion.",
            value=currency,
        ),
        ProjectionNote(code="no_inflation", label="Aucune inflation ni hausse de prix."),
        ProjectionNote(
            code="no_starting_balance",
            label="Sans connexion bancaire, pas de solde de départ : le solde d'un mois est "
            "la différence entre ses revenus et ses dépenses.",
        ),
        ProjectionNote(
            code="savings",
            label="Objectifs d'épargne : montant épargné = dernière valeur relevée ; versement "
            "déclaré, sinon solde moyen prévu ; premier versement le mois prochain.",
        ),
    ]
    return notes
