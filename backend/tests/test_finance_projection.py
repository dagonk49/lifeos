"""Recurring items, savings goals and local projections (task 26): known, scheduled and
estimated amounts kept apart, exact, recomputed after every correction."""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.db.session import Database
from lifeos.modules.agents.context import _finances as finance_context
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.finance import tracking
from lifeos.modules.finance.projection import (
    Recurring,
    SavingsGoal,
    add_months,
    baseline_window,
    month_start,
    occurrences,
    project,
)
from lifeos.modules.guardian.principal import agent_principal
from lifeos.modules.identity.models import User
from lifeos.modules.llm.fake import FakeProvider

from .conftest import Owner, make_ctx

SPACE = uuid.uuid4()


def _rule(cadence: str, start: date, **fields: Any) -> Recurring:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "label": "Loyer",
        "direction": "expense",
        "amount": Decimal("750.00"),
        "cadence": cadence,
        "start_date": start,
        **fields,
    }
    return Recurring(**values)


# --- Pure: occurrences -------------------------------------------------------------------------


def test_occurrences_by_cadence() -> None:
    monthly = _rule("monthly", date(2026, 1, 31))
    assert occurrences(monthly, date(2026, 2, 1), date(2026, 4, 30)) == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    assert occurrences(monthly, date(2024, 2, 1), date(2024, 2, 29)) == []  # before its start
    leap = _rule("monthly", date(2024, 1, 31))
    assert occurrences(leap, date(2024, 2, 1), date(2024, 2, 29)) == [date(2024, 2, 29)]
    quarterly = _rule("quarterly", date(2026, 1, 15))
    assert occurrences(quarterly, date(2026, 1, 1), date(2026, 12, 31)) == [
        date(2026, 1, 15),
        date(2026, 4, 15),
        date(2026, 7, 15),
        date(2026, 10, 15),
    ]
    yearly = _rule("yearly", date(2024, 2, 29))
    assert occurrences(yearly, date(2025, 1, 1), date(2026, 12, 31)) == [
        date(2025, 2, 28),
        date(2026, 2, 28),
    ]
    once = _rule("once", date(2026, 11, 20))
    assert occurrences(once, date(2026, 11, 1), date(2026, 11, 30)) == [date(2026, 11, 20)]
    assert occurrences(once, date(2026, 12, 1), date(2026, 12, 31)) == []
    ended = _rule("monthly", date(2026, 1, 5), end_date=date(2026, 3, 4))
    assert occurrences(ended, date(2026, 1, 1), date(2026, 6, 30)) == [
        date(2026, 1, 5),
        date(2026, 2, 5),
    ]
    weekly = _rule("weekly", date(2026, 10, 1))
    assert occurrences(weekly, date(2026, 10, 10), date(2026, 10, 31)) == [
        date(2026, 10, 15),
        date(2026, 10, 22),
        date(2026, 10, 29),
    ]
    paused = _rule("monthly", date(2026, 1, 5), active=False)
    assert occurrences(paused, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_baseline_window_follows_the_history() -> None:
    today = date(2026, 10, 27)
    assert baseline_window(today, 3, date(2025, 1, 3)) == (date(2026, 7, 1), date(2026, 9, 1), 3)
    assert baseline_window(today, 3, date(2026, 9, 10)) == (date(2026, 9, 1), date(2026, 9, 1), 1)
    assert baseline_window(today, 3, date(2026, 10, 2)) is None
    assert baseline_window(today, 3, None) is None


# --- Pure: projection --------------------------------------------------------------------------


def _project(**overrides: Any) -> Any:
    food = uuid.UUID(int=1)
    values: dict[str, Any] = {
        "space_id": SPACE,
        "currency": "EUR",
        "today": date(2026, 10, 27),
        "horizon": 2,
        "baseline_months": 3,
        "first_month": date(2026, 1, 1),
        # Groceries: 300, 360 and 330 over July to September; salary is recurring.
        "history": {
            ("2026-07", "expense", food): Decimal("300.00"),
            ("2026-08", "expense", food): Decimal("360.00"),
            ("2026-09", "expense", food): Decimal("330.00"),
        },
        "current_known": {"expense": Decimal("1000.00"), "income": Decimal("2000.00")},
        "current_spent": {("expense", food): Decimal("250.00")},
        "realised": {},
        "recurring": [],
        "category_names": {food: "Alimentation"},
        "goals": None,
    }
    values.update(overrides)
    return project(**values)


def test_projection_keeps_known_scheduled_and_estimated_apart() -> None:
    salary = _rule(
        "monthly", date(2026, 1, 28), label="Salaire", direction="income", amount=Decimal("2000.00")
    )
    rent = _rule("monthly", date(2026, 1, 5))
    power = _rule(
        "monthly",
        date(2026, 1, 12),
        label="Électricité",
        amount=Decimal("80.00"),
        is_estimate=True,
        low=Decimal("60.00"),
        high=Decimal("120.00"),
    )
    result = _project(
        recurring=[salary, rent, power], realised={salary.id: 1, rent.id: 1, power.id: 1}
    )
    current, november, december = result.months
    assert (current.month, current.state, november.state) == ("2026-10", "current", "future")
    # This month: recorded amounts are known, never estimated again.
    assert (current.known_income, current.known_expenses) == (
        Decimal("2000.00"),
        Decimal("1000.00"),
    )
    assert current.scheduled_income == current.scheduled_expenses == Decimal("0.00")
    # Groceries: mean 330 (300..360), of which 250 already spent.
    assert (current.estimated_expenses, current.estimated_expenses_low) == (
        Decimal("80.00"),
        Decimal("50.00"),
    )
    assert current.estimated_expenses_high == Decimal("110.00")
    assert {i.basis for i in current.items if i.source == "transactions"} == {"known"}

    # Next month: rent and salary scheduled, power and groceries estimated with ranges.
    assert november.known_expenses == Decimal("0.00")
    assert november.scheduled_expenses == Decimal("750.00")
    assert november.scheduled_income == Decimal("2000.00")
    assert november.estimated_expenses == Decimal("410.00")
    assert (november.estimated_expenses_low, november.estimated_expenses_high) == (
        Decimal("360.00"),
        Decimal("480.00"),
    )
    assert november.balance == Decimal("840.00")
    assert (november.balance_low, november.balance_high) == (Decimal("770.00"), Decimal("890.00"))
    bases = {i.label: i.basis for i in november.items}
    assert bases == {
        "Salaire": "scheduled",
        "Loyer": "scheduled",
        "Électricité": "estimated",
        "Alimentation": "estimated",
    }
    assert december.cumulative == current.balance + november.balance + december.balance
    assert all(
        isinstance(m.balance, Decimal) and m.balance.as_tuple().exponent == -2
        for m in result.months
    )
    assert result.warnings == []
    codes = [a.code for a in result.assumptions]
    assert codes[:2] == ["baseline_window", "range"]
    assert "no_inflation" in codes and "no_starting_balance" in codes


def test_short_history_widens_the_range_and_warns() -> None:
    food = uuid.UUID(int=1)
    result = _project(
        first_month=date(2026, 9, 3),
        history={("2026-09", "expense", food): Decimal("400.00")},
        current_known={},
        current_spent={},
        other_currency=2,
    )
    november = result.months[1]
    assert november.estimated_expenses == Decimal("400.00")
    assert (november.estimated_expenses_low, november.estimated_expenses_high) == (
        Decimal("300.00"),
        Decimal("500.00"),
    )
    assert [w.code for w in result.warnings] == ["short_history", "other_currency"]
    nothing = _project(first_month=None, history={}, current_known={}, current_spent={})
    assert [w.code for w in nothing.warnings] == ["no_history"]
    assert nothing.months[1].estimated_expenses == Decimal("0.00")


def test_savings_goals_eta_declared_estimated_and_late() -> None:
    salary = _rule(
        "monthly", date(2026, 1, 28), label="Salaire", direction="income", amount=Decimal("2000.00")
    )
    goals = [
        SavingsGoal(
            id=uuid.uuid4(),
            title="Vacances",
            target=Decimal("1000.00"),
            saved=Decimal("400.00"),
            contribution=Decimal("200.00"),
            due_date=date(2027, 3, 31),
        ),
        SavingsGoal(
            id=uuid.uuid4(),
            title="Voiture",
            target=Decimal("10000.00"),
            saved=Decimal("0.00"),
            due_date=date(2027, 1, 31),
        ),
        SavingsGoal(
            id=uuid.uuid4(), title="Fait", target=Decimal("100.00"), saved=Decimal("150.00")
        ),
    ]
    result = _project(recurring=[salary], goals=goals, realised={salary.id: 1})
    holidays, car, done = result.savings_goals
    # Declared: 600 left at 200 a month -> 3 months from next month.
    assert (holidays.contribution_source, holidays.contribution) == ("declared", Decimal("200.00"))
    assert holidays.eta_month == holidays.eta_early == holidays.eta_late == "2027-01"
    assert holidays.on_track is True
    # Estimated from the projected balance (2000 - 330 = 1670, range 1640..1700).
    assert car.contribution_source == "estimated"
    assert car.contribution == Decimal("1670.00")
    # 10000 / 1670 -> 6 months; at the low balance (1640) 7 months.
    assert (car.eta_month, car.eta_early, car.eta_late) == ("2027-04", "2027-04", "2027-05")
    assert car.on_track is False  # due in January 2027
    assert done.reached is True and done.remaining == Decimal("0.00")
    assert "no_contribution" in [w.code for w in result.warnings]


# --- API -----------------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(ZoneInfo("Europe/Paris")).date()


def _in_month(offset: int, day: int = 4) -> str:
    return add_months(month_start(_today()), offset).replace(day=day).isoformat()


async def _tx(client: AsyncClient, space: str, **fields: Any) -> dict[str, Any]:
    body = {"space_id": space, "amount": "10.00", "tx_date": _in_month(-1), **fields}
    response = await client.post("/v1/finance/transactions", json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def _projection(client: AsyncClient, space: str, **params: str) -> dict[str, Any]:
    response = await client.get("/v1/finance/projection", params={"space_id": space, **params})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_projection_is_recomputed_after_each_correction(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = spaces["personal"]
    categories = (
        await client.post("/v1/finance/categories/defaults", json={"space_id": personal})
    ).json()
    food = next(c["id"] for c in categories if c["name"] == "Alimentation")
    leisure = next(c["id"] for c in categories if c["name"] == "Loisirs")
    corrected = None
    for offset, amount in ((-3, "300.00"), (-2, "360.00"), (-1, "330.00")):
        corrected = await _tx(
            client, personal, amount=amount, category_id=food, tx_date=_in_month(offset)
        )
    assert corrected is not None
    before = await _projection(client, personal)
    assert before["baseline"]["months"] == 3
    next_month = before["months"][1]
    assert next_month["estimated_expenses"] == "330.00"
    assert {i["basis"] for i in next_month["items"]} == {"estimated"}

    # Correcting an amount changes the projection at once (nothing is stored).
    patched = await client.patch(
        f"/v1/finance/transactions/{corrected['id']}", json={"amount": "390.00"}
    )
    assert patched.status_code == 200
    after = await _projection(client, personal)
    assert after["months"][1]["estimated_expenses"] == "350.00"
    # Recategorised: the estimate moves to the other category.
    await client.patch(f"/v1/finance/transactions/{corrected['id']}", json={"category_id": leisure})
    items = {
        i["label"]: i["amount"] for i in (await _projection(client, personal))["months"][1]["items"]
    }
    assert items == {"Alimentation": "220.00", "Loisirs": "130.00"}

    # A recurring item is « prévu » (fixed) or « estimé » (variable), with its range.
    rent = await client.post(
        "/v1/finance/recurring",
        json={
            "space_id": personal,
            "label": "Loyer",
            "amount": "750",
            "start_date": _in_month(-6, 5),
        },
    )
    assert rent.status_code == 201, rent.text
    assert rent.json()["next_date"] is not None
    month = (await _projection(client, personal))["months"][1]
    assert month["scheduled_expenses"] == "750.00"
    await client.patch(
        f"/v1/finance/recurring/{rent.json()['id']}",
        json={
            "amount": "800",
            "amount_is_estimate": True,
            "amount_low": "780",
            "amount_high": "820",
        },
        headers={"If-Match": str(rent.json()["version"])},
    )
    month = (await _projection(client, personal))["months"][1]
    assert month["scheduled_expenses"] == "0.00"
    rent_item = next(i for i in month["items"] if i["label"] == "Loyer")
    assert (rent_item["basis"], rent_item["amount"], rent_item["low"], rent_item["high"]) == (
        "estimated",
        "800.00",
        "780.00",
        "820.00",
    )

    # A savings goal follows its check-ins (the saved amount is known, never estimated).
    goal = await client.post(
        "/v1/goals",
        json={
            "space_id": personal,
            "title": "Fonds de secours",
            "kind": "measurable",
            "metric_unit": "€",
            "currency": "EUR",
            "start_value": "0",
            "target_value": "3000",
            "monthly_contribution": "500",
        },
    )
    assert goal.status_code == 201, goal.text
    saving = (await _projection(client, personal))["savings_goals"][0]
    assert (saving["saved"], saving["remaining"], saving["contribution_source"]) == (
        "0.00",
        "3000.00",
        "declared",
    )
    eta_before = saving["eta_month"]
    checkin = await client.post(f"/v1/goals/{goal.json()['id']}/checkins", json={"value": "1500"})
    assert checkin.status_code == 201, checkin.text
    saving = (await _projection(client, personal))["savings_goals"][0]
    assert (saving["saved"], saving["remaining"]) == ("1500.00", "1500.00")
    assert saving["saved_at"] == _today().isoformat()
    assert saving["eta_month"] < eta_before


async def test_known_amounts_other_currencies_and_linked_operations(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = spaces["personal"]
    rent = (
        await client.post(
            "/v1/finance/recurring",
            json={
                "space_id": personal,
                "label": "Loyer",
                "amount": "750",
                "start_date": _in_month(-6, 1),
            },
        )
    ).json()
    # Rent paid every month, linked to its recurring item: never in the average.
    for offset in (-3, -2, -1, 0):
        await _tx(
            client, personal, amount="750", tx_date=_in_month(offset, 1), recurring_id=rent["id"]
        )
    await _tx(client, personal, amount="40", tx_date=_in_month(-1))
    await _tx(client, personal, amount="99", currency="USD", tx_date=_in_month(-1))
    result = await _projection(client, personal)
    current, following = result["months"][0], result["months"][1]
    # This month's rent is recorded (known), so not scheduled a second time.
    assert current["known_expenses"] == "750.00"
    assert current["scheduled_expenses"] == "0.00"
    assert following["scheduled_expenses"] == "750.00"
    # The average only holds the 40 (over 3 months), not the linked rent.
    assert following["estimated_expenses"] == "13.33"
    assert [w["code"] for w in result["warnings"]] == ["other_currency"]
    known = [i for m in result["months"] for i in m["items"] if i["source"] == "transactions"]
    assert all(i["basis"] == "known" for i in known)

    wrong = await client.post(
        "/v1/finance/transactions",
        json={
            "space_id": personal,
            "amount": "5",
            "tx_date": _in_month(-1),
            "direction": "income",
            "recurring_id": rent["id"],
        },
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "recurring_direction_mismatch"
    other = await client.post(
        "/v1/finance/transactions",
        json={
            "space_id": spaces["professional"],
            "amount": "5",
            "tx_date": _in_month(-1),
            "recurring_id": rent["id"],
        },
    )
    assert other.json()["code"] == "recurring_space_mismatch"


async def test_recurring_crud_rules(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    categories = (
        await client.post("/v1/finance/categories/defaults", json={"space_id": personal})
    ).json()
    salary = next(c["id"] for c in categories if c["name"] == "Salaire")
    body = {
        "space_id": personal,
        "label": "Abonnement",
        "amount": "12.99",
        "merchant": "Musique",
        "start_date": "2026-01-10",
        "category_id": salary,
    }
    mismatch = await client.post("/v1/finance/recurring", json=body)
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "category_kind_mismatch"
    body.pop("category_id")
    created = await client.post(
        "/v1/finance/recurring", json=body, headers={"Idempotency-Key": "r-1"}
    )
    assert created.status_code == 201
    replay = await client.post(
        "/v1/finance/recurring", json=body, headers={"Idempotency-Key": "r-1"}
    )
    assert replay.json()["id"] == created.json()["id"]
    item = created.json()
    range_without_estimate = await client.patch(
        f"/v1/finance/recurring/{item['id']}", json={"amount_low": "10"}
    )
    assert range_without_estimate.json()["code"] == "recurring_range_invalid"
    dates = await client.patch(
        f"/v1/finance/recurring/{item['id']}", json={"end_date": "2025-01-01"}
    )
    assert dates.json()["code"] == "recurring_dates_invalid"
    ok = await client.patch(
        f"/v1/finance/recurring/{item['id']}", json={"active": False}, headers={"If-Match": "1"}
    )
    assert ok.status_code == 200 and ok.json()["next_date"] is None
    stale = await client.patch(
        f"/v1/finance/recurring/{item['id']}", json={"label": "X"}, headers={"If-Match": "1"}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"
    assert (await client.get(f"/v1/finance/recurring?space_id={personal}")).json() == []
    listed = (await client.get("/v1/finance/recurring?include_inactive=true")).json()
    assert [r["id"] for r in listed] == [item["id"]]
    exported = (await client.get("/v1/privacy/export")).json()
    assert [r["id"] for r in exported["finance_recurring"]] == [item["id"]]
    assert (await client.delete(f"/v1/finance/recurring/{item['id']}")).status_code == 204
    assert (await client.get(f"/v1/finance/recurring/{item['id']}")).status_code in (404, 405)

    events = list(
        await db.scalars(select(AuditEvent).where(AuditEvent.action.like("finance.recurring.%")))
    )
    assert {e.action for e in events} == {
        "finance.recurring.create",
        "finance.recurring.update",
        "finance.recurring.delete",
    }
    dumped = json.dumps([e.details for e in events], default=str)
    assert "12.99" not in dumped and "Musique" not in dumped and "Abonnement" not in dumped


async def test_agent_without_goals_permission_gets_no_savings(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    await client.post(
        "/v1/goals",
        json={
            "space_id": personal,
            "title": "Épargne",
            "kind": "measurable",
            "currency": "EUR",
            "start_value": "0",
            "target_value": "1000",
        },
    )
    await client.post(
        "/v1/guardian/permissions",
        json={
            "agent": "finance",
            "space_id": personal,
            "resource": "finances",
            "purpose": "Assistant Finances",
        },
    )
    owner = await db.get(User, logged_in.user.id)
    assert owner is not None
    agent = await agent_principal(db, owner.id, "finance")
    agent_ctx = await make_ctx(database, db, settings, owner, fake_llm, principal=agent)
    result = await tracking.projection(agent_ctx, space_id=uuid.UUID(personal))
    assert result.savings_goals is None
    # The finance agent gets the projection with its assumptions, never the goals here.
    sections = await finance_context(agent_ctx, uuid.UUID(personal))
    projected = next(s for s in sections if s.startswith("PROJECTION"))
    assert "Hypothèses et limites" in projected and "Épargne" not in projected
    own = await _projection(client, personal)
    assert [g["title"] for g in own["savings_goals"]] == ["Épargne"]

    # Isolation: another space of the owner has its own projection, without this goal.
    pro = await _projection(client, spaces["professional"])
    assert pro["savings_goals"] == []


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"kind": "qualitative", "currency": "EUR"}, 422),
        (
            {
                "kind": "measurable",
                "start_value": "0",
                "target_value": "100",
                "monthly_contribution": "10",
            },
            422,
        ),
        (
            {"kind": "measurable", "start_value": "100", "target_value": "50", "currency": "EUR"},
            422,
        ),
        (
            {
                "kind": "measurable",
                "start_value": "0",
                "target_value": "100",
                "currency": "EUR",
                "monthly_contribution": "10",
            },
            201,
        ),
    ],
)
async def test_savings_goal_validation(
    client: AsyncClient, spaces: dict[str, str], body: dict[str, Any], status: int
) -> None:
    response = await client.post(
        "/v1/goals", json={"space_id": spaces["personal"], "title": "Épargne", **body}
    )
    assert response.status_code == status, response.text
    if status == 201:
        goal = response.json()
        assert (goal["currency"], goal["monthly_contribution"]) == ("EUR", "10.00")
        cleared = await client.patch(f"/v1/goals/{goal['id']}", json={"currency": None})
        assert cleared.json()["code"] == "goal_contribution_without_currency"
        both = await client.patch(
            f"/v1/goals/{goal['id']}", json={"currency": None, "monthly_contribution": None}
        )
        assert both.status_code == 200
        assert both.json()["currency"] is None
