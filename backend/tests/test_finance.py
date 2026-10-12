import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.db.session import Database
from lifeos.modules.agents.registry import suggest_agent
from lifeos.modules.documents.models import Document
from lifeos.modules.finance import service as finance
from lifeos.modules.finance.models import CategoryRule
from lifeos.modules.finance.receipts import parse_receipt
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.principal import agent_principal
from lifeos.modules.identity.models import Space, User
from lifeos.modules.llm.fake import FakeProvider

from .conftest import Owner, enable_cloud, make_ctx

RECEIPT_TEXT = """CARREFOUR MARKET
12 rue de la Paix
75002 PARIS
LAIT DEMI ECREME 1L        1,15
PAIN DE CAMPAGNE           2,30
POMMES GOLDEN 1,2 KG       3,48
SOUS-TOTAL                 6,93
REMISE FIDELITE           -0,50
TOTAL TTC                  6,43 EUR
TVA 5,5 %   6,09   0,34
CB EMV  **** **** **** 1234
10/10/2026 18:42  CAISSE 3"""


def _paris_today() -> date:
    return datetime.now(ZoneInfo("Europe/Paris")).date()


async def _defaults(client: AsyncClient, space: str) -> dict[str, str]:
    response = await client.post("/v1/finance/categories/defaults", json={"space_id": space})
    assert response.status_code == 200, response.text
    return {c["name"]: c["id"] for c in response.json()}


async def _tx(client: AsyncClient, space: str, **fields: Any) -> dict[str, Any]:
    body = {"space_id": space, "amount": "10.00", "tx_date": "2026-09-15", **fields}
    response = await client.post("/v1/finance/transactions", json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def _document(
    db: AsyncSession,
    owner: Owner,
    space: str,
    *,
    title: str,
    purpose: str = "receipt",
    status: str = "ready",
    analysis: dict[str, Any] | None = None,
    sensitivity: list[str] | None = None,
    text: str | None = None,
) -> str:
    document = Document(
        user_id=owner.user.id,
        space_id=uuid.UUID(space),
        title=title,
        kind="image",
        mime="image/jpeg",
        size_bytes=2048,
        sha256=uuid.uuid4().hex * 2,
        purpose=purpose,
        status=status,
        analysis=analysis,
        sensitivity=sensitivity or [],
        extracted_text=text,
    )
    db.add(document)
    await db.commit()
    return str(document.id)


def _receipt_analysis() -> dict[str, Any]:
    return {"receipt": parse_receipt(RECEIPT_TEXT, date(2026, 10, 11)).as_dict()}


# --- Pure helpers ----------------------------------------------------------------------------


def test_merchant_key_month_bounds_and_local_today() -> None:
    assert finance.merchant_key("CARREFOUR MARKET 1234") == "carrefour market"
    assert finance.merchant_key("Carrefour-Market") == "carrefour market"
    assert finance.merchant_key("Boulangerie du Marché") == "boulangerie du marche"
    assert finance.merchant_key(" 12/10 ") is None
    assert finance.merchant_key(None) is None
    assert finance.month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert finance.month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert finance.month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    with pytest.raises(ProblemError):
        finance.month_bounds("2026-13")

    # 22:30 UTC on 30 September is already 1 October in Paris, still 30 September in Paris
    # an hour earlier in UTC-10: the month is the owner's local one.
    instant = datetime(2026, 9, 30, 22, 30, tzinfo=UTC)
    paris = cast(Ctx, SimpleNamespace(user=SimpleNamespace(timezone="Europe/Paris")))
    honolulu = cast(Ctx, SimpleNamespace(user=SimpleNamespace(timezone="Pacific/Honolulu")))
    assert finance.local_today(paris, instant) == date(2026, 10, 1)
    assert finance.local_today(honolulu, instant) == date(2026, 9, 30)


def test_finance_agent_suggestion() -> None:
    assert suggest_agent("Combien ai-je dépensé ce mois-ci ? Mon budget est serré") == "finance"
    assert suggest_agent("Comment économiser un peu d'argent ?") == "finance"
    # Unchanged for the other agents.
    assert suggest_agent("Aide-moi à planifier ma semaine, j'ai trop de tâches") == "planning"
    assert suggest_agent("Bonjour") is None


# --- Categories ------------------------------------------------------------------------------


async def test_categories_crud_uniqueness_and_idempotent_defaults(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal, pro = spaces["personal"], spaces["professional"]
    assert (await client.get(f"/v1/finance/categories?space_id={personal}")).json() == []
    first = await _defaults(client, personal)
    assert len(first) == 11
    listed = (await client.get(f"/v1/finance/categories?space_id={personal}")).json()
    assert [c["kind"] for c in listed] == ["expense"] * 9 + ["income"] * 2
    assert {"Alimentation", "Santé", "Abonnements", "Salaire", "Autres revenus"} <= set(first)
    # Idempotent: the same categories, no duplicates.
    assert await _defaults(client, personal) == first

    duplicate = await client.post(
        "/v1/finance/categories", json={"space_id": personal, "name": "alimentation"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "category_exists"
    created = await client.post(
        "/v1/finance/categories",
        json={"space_id": personal, "name": " Vacances ", "monthly_budget": "300"},
    )
    assert created.status_code == 201, created.text
    vacation = created.json()
    assert vacation["name"] == "Vacances" and vacation["monthly_budget"] == "300.00"
    # Unique per space only.
    same_name_pro = await client.post(
        "/v1/finance/categories", json={"space_id": pro, "name": "Vacances"}
    )
    assert same_name_pro.status_code == 201

    negative = await client.post(
        "/v1/finance/categories",
        json={"space_id": personal, "name": "Dettes", "monthly_budget": "-1"},
    )
    assert negative.status_code == 422
    too_precise = await client.post(
        "/v1/finance/categories",
        json={"space_id": personal, "name": "Dettes", "monthly_budget": "1.001"},
    )
    assert too_precise.status_code == 422

    renamed = await client.patch(
        f"/v1/finance/categories/{vacation['id']}", json={"name": "Voyages"}
    )
    assert renamed.json()["name"] == "Voyages"
    clash = await client.patch(
        f"/v1/finance/categories/{vacation['id']}", json={"name": "Logement"}
    )
    assert clash.status_code == 409 and clash.json()["code"] == "category_exists"
    unbudgeted = await client.patch(
        f"/v1/finance/categories/{vacation['id']}", json={"monthly_budget": None}
    )
    assert unbudgeted.json()["monthly_budget"] is None
    archived = await client.patch(
        f"/v1/finance/categories/{vacation['id']}", json={"archived": True}
    )
    assert archived.json()["archived"] is True
    active = (await client.get(f"/v1/finance/categories?space_id={personal}")).json()
    assert "Voyages" not in [c["name"] for c in active]
    everything = (
        await client.get(f"/v1/finance/categories?space_id={personal}&include_archived=true")
    ).json()
    assert "Voyages" in [c["name"] for c in everything]

    # A category in use cannot change kind; deleting it leaves transactions uncategorised.
    food = first["Alimentation"]
    tx = await _tx(client, personal, category_id=food)
    kind_change = await client.patch(f"/v1/finance/categories/{food}", json={"kind": "income"})
    assert kind_change.status_code == 409 and kind_change.json()["code"] == "category_in_use"
    assert (await client.delete(f"/v1/finance/categories/{food}")).status_code == 204
    after = (await client.get(f"/v1/finance/transactions/{tx['id']}")).json()
    assert after["category_id"] is None
    assert (await client.delete(f"/v1/finance/categories/{food}")).status_code == 404


# --- Transactions ----------------------------------------------------------------------------


async def test_transaction_validation(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, logged_in: Owner
) -> None:
    personal, pro = spaces["personal"], spaces["professional"]
    categories = await _defaults(client, personal)
    pro_categories = await _defaults(client, pro)

    async def refused(status: int, **fields: Any) -> dict[str, Any]:
        body = {"space_id": personal, "amount": "10.00", "tx_date": "2026-09-15", **fields}
        response = await client.post("/v1/finance/transactions", json=body)
        assert response.status_code == status, response.text
        result: dict[str, Any] = response.json()
        return result

    await refused(422, amount="-5")
    await refused(422, amount="0")
    await refused(422, amount="12.345")
    await refused(422, amount="NaN")
    await refused(422, currency="eur")
    tomorrow = (_paris_today() + timedelta(days=1)).isoformat()
    assert (await refused(422, tx_date=tomorrow))["code"] == "tx_date_in_future"
    mismatch = await refused(422, category_id=pro_categories["Divers"])
    assert mismatch["code"] == "category_space_mismatch"
    kind = await refused(422, category_id=categories["Salaire"])
    assert kind["code"] == "category_kind_mismatch"
    await refused(404, category_id=str(uuid.uuid4()))
    no_document = await refused(422, source_type="receipt")
    assert no_document["code"] == "receipt_document_required"

    pro_receipt = await _document(db, logged_in, pro, title="ticket-pro.jpg")
    note = await _document(db, logged_in, personal, title="note.jpg", purpose="document")
    other_space = await refused(422, source_type="receipt", document_id=pro_receipt)
    assert other_space["code"] == "document_space_mismatch"
    assert (await refused(422, document_id=note))["code"] == "not_a_receipt"
    await refused(404, document_id=str(uuid.uuid4()))

    ok = await _tx(
        client,
        personal,
        amount="12.3",
        tx_date=_paris_today().isoformat(),
        merchant="  Boulangerie   du Marché ",
        notes="  Pour le\ndimanche ",
    )
    assert ok["amount"] == "12.30" and ok["currency"] == "EUR"
    assert ok["merchant"] == "Boulangerie du Marché"
    assert ok["notes"] == "Pour le\ndimanche"
    assert ok["category_id"] is None and ok["category_confirmed"] is False
    assert ok["source_type"] == "manual" and ok["version"] == 1


async def test_today_is_the_owner_local_day(client: AsyncClient, spaces: dict[str, str]) -> None:
    # UTC+14: "today" there is often tomorrow in UTC, and must still be accepted.
    tz = "Pacific/Kiritimati"
    assert (await client.patch("/v1/settings", json={"timezone": tz})).status_code == 200
    local = datetime.now(ZoneInfo(tz)).date()
    await _tx(client, spaces["personal"], tx_date=local.isoformat())
    future = await client.post(
        "/v1/finance/transactions",
        json={
            "space_id": spaces["personal"],
            "amount": "1",
            "tx_date": (local + timedelta(days=1)).isoformat(),
        },
    )
    assert future.status_code == 422
    summary = (await client.get(f"/v1/finance/summary?space_id={spaces['personal']}")).json()
    assert summary["month"] == f"{local:%Y-%m}"
    assert summary["totals"]["expenses"] == "10.00"


async def test_learned_rule_classifies_automatically_and_stays_correctable(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    space = spaces["personal"]
    categories = await _defaults(client, space)
    food, restaurants = categories["Alimentation"], categories["Restaurants"]

    first = await _tx(client, space, merchant="CARREFOUR MARKET 75002")
    assert first["category_id"] is None
    corrected = await client.patch(
        f"/v1/finance/transactions/{first['id']}",
        json={"category_id": food},
        headers={"If-Match": str(first["version"])},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["category_id"] == food
    assert corrected.json()["category_confirmed"] is True
    assert corrected.json()["version"] == 2
    stale = await client.patch(
        f"/v1/finance/transactions/{first['id']}",
        json={"label": "Courses"},
        headers={"If-Match": "1"},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    # Same merchant, written differently: classified automatically, not confirmed.
    second = await _tx(client, space, merchant="Carrefour Market")
    assert second["category_id"] == food
    assert second["category_confirmed"] is False
    rule = await db.scalar(
        select(CategoryRule).where(CategoryRule.merchant_key == "carrefour market")
    )
    assert rule is not None and rule.hits == 1

    # The owner corrects it: the rule follows the owner's latest choice.
    await client.patch(
        f"/v1/finance/transactions/{second['id']}", json={"category_id": restaurants}
    )
    third = await _tx(client, space, merchant="carrefour-market")
    assert third["category_id"] == restaurants
    await db.refresh(rule)
    assert rule.category_id == uuid.UUID(restaurants) and rule.hits == 1

    # A refund (income) never gets an expense category.
    refund = await _tx(client, space, merchant="Carrefour Market", direction="income")
    assert refund["category_id"] is None

    # A category chosen at creation is confirmed and teaches the rule too.
    bistro = await _tx(client, space, merchant="Le Petit Bistrot", category_id=restaurants)
    assert bistro["category_confirmed"] is True
    again = await _tx(client, space, merchant="LE PETIT BISTROT")
    assert again["category_id"] == restaurants and again["category_confirmed"] is False

    # Fixing the merchant of an unclassified transaction applies the rule (one new version).
    unknown = await _tx(client, space, merchant="Ticket illisible")
    assert unknown["category_id"] is None
    renamed = (
        await client.patch(
            f"/v1/finance/transactions/{unknown['id']}", json={"merchant": "Le Petit Bistrot"}
        )
    ).json()
    assert renamed["category_id"] == restaurants and renamed["category_confirmed"] is False
    assert renamed["version"] == 2

    # Removing the category: uncategorised, and the rule is not touched.
    cleared = await client.patch(
        f"/v1/finance/transactions/{again['id']}", json={"category_id": None}
    )
    assert cleared.json()["category_id"] is None
    assert cleared.json()["category_confirmed"] is False
    # Changing the direction drops an automatic category that no longer fits...
    flipped = await client.patch(
        f"/v1/finance/transactions/{third['id']}", json={"direction": "income"}
    )
    assert flipped.status_code == 200 and flipped.json()["category_id"] is None
    # ...but refuses to contradict the owner's own choice.
    confirmed = await client.patch(
        f"/v1/finance/transactions/{bistro['id']}", json={"direction": "income"}
    )
    assert confirmed.status_code == 422
    assert confirmed.json()["code"] == "category_kind_mismatch"
    assert (await client.delete(f"/v1/finance/transactions/{bistro['id']}")).status_code == 204
    assert (await client.get(f"/v1/finance/transactions/{bistro['id']}")).status_code == 404


async def test_transaction_list_filters_and_pagination(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    space = spaces["personal"]
    categories = await _defaults(client, space)
    food = categories["Alimentation"]
    await _tx(client, space, tx_date="2026-08-31", merchant="Boulangerie Paul", amount="3.10")
    await _tx(client, space, tx_date="2026-09-01", merchant="Monoprix", category_id=food)
    await _tx(client, space, tx_date="2026-09-20", label="Remboursement", direction="income")
    await _tx(client, space, tx_date="2026-09-30", merchant="Boulangerie Paul", amount="2.20")
    await _tx(client, spaces["professional"], tx_date="2026-09-10", merchant="Papeterie")

    def dates(page: dict[str, Any]) -> list[str]:
        return [t["tx_date"] for t in page["items"]]

    september = (
        await client.get(f"/v1/finance/transactions?space_id={space}&month=2026-09")
    ).json()
    assert dates(september) == ["2026-09-30", "2026-09-20", "2026-09-01"]
    by_category = (
        await client.get(f"/v1/finance/transactions?space_id={space}&category_id={food}")
    ).json()
    assert dates(by_category) == ["2026-09-01"]
    income = (
        await client.get(f"/v1/finance/transactions?space_id={space}&direction=income")
    ).json()
    assert [t["label"] for t in income["items"]] == ["Remboursement"]
    uncategorised = (
        await client.get(f"/v1/finance/transactions?space_id={space}&uncategorised=true")
    ).json()
    assert len(uncategorised["items"]) == 3
    found = (await client.get(f"/v1/finance/transactions?space_id={space}&q=boulangerie")).json()
    assert [t["amount"] for t in found["items"]] == ["2.20", "3.10"]

    first = (await client.get(f"/v1/finance/transactions?space_id={space}&limit=3")).json()
    assert dates(first) == ["2026-09-30", "2026-09-20", "2026-09-01"]
    assert first["next_cursor"]
    rest = (
        await client.get(
            f"/v1/finance/transactions?space_id={space}&limit=3&cursor={first['next_cursor']}"
        )
    ).json()
    assert dates(rest) == ["2026-08-31"] and rest["next_cursor"] is None
    # All spaces by default.
    assert len((await client.get("/v1/finance/transactions")).json()["items"]) == 5

    bad_month = await client.get(f"/v1/finance/transactions?space_id={space}&month=2026-13")
    assert bad_month.status_code == 422
    bad_cursor = await client.get("/v1/finance/transactions?cursor=nope")
    assert bad_cursor.status_code == 400


# --- Budget summary --------------------------------------------------------------------------


async def test_summary_uses_exact_decimals_and_month_boundaries(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    space = spaces["personal"]
    categories = await _defaults(client, space)
    for name, budget in (("Alimentation", "100"), ("Restaurants", "50"), ("Loisirs", "20")):
        await client.patch(
            f"/v1/finance/categories/{categories[name]}", json={"monthly_budget": budget}
        )
    food, restaurants = categories["Alimentation"], categories["Restaurants"]
    await _tx(client, space, amount="0.10", category_id=food)
    await _tx(client, space, amount="0.20", category_id=food, tx_date="2026-09-30")
    await _tx(client, space, amount="45", category_id=restaurants, tx_date="2026-09-01")
    await _tx(client, space, amount="25", category_id=categories["Loisirs"])
    await _tx(client, space, amount="3.33")  # uncategorised
    await _tx(client, space, amount="2000", direction="income", category_id=categories["Salaire"])
    # Outside the month (both sides) or in another currency: not counted.
    await _tx(client, space, amount="999", category_id=food, tx_date="2026-08-31")
    await _tx(client, space, amount="999", category_id=food, tx_date="2026-10-01")
    await _tx(client, space, amount="12", category_id=food, currency="USD")
    await _tx(client, spaces["professional"], amount="999")

    response = await client.get(f"/v1/finance/summary?space_id={space}&month=2026-09")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["period_start"] == "2026-09-01" and summary["period_end"] == "2026-09-30"
    rows = {r["name"]: r for r in summary["categories"]}
    assert rows["Alimentation"]["spent"] == "0.30"  # exactly, not 0.30000000000000004
    assert rows["Alimentation"]["remaining"] == "99.70"
    assert rows["Alimentation"]["ratio"] == 0.003
    assert rows["Alimentation"]["transactions"] == 2
    assert rows["Restaurants"]["ratio"] == 0.9 and rows["Restaurants"]["over_budget"] is False
    assert rows["Loisirs"]["remaining"] == "-5.00" and rows["Loisirs"]["over_budget"] is True
    assert rows["Santé"]["spent"] == "0.00" and rows["Santé"]["ratio"] is None
    assert rows["Salaire"]["received"] == "2000.00"
    assert summary["uncategorised"] == {"expenses": "3.33", "income": "0.00", "transactions": 1}
    assert summary["totals"] == {
        "expenses": "73.63",
        "income": "2000.00",
        "balance": "1926.37",
        "budget": "170.00",
        "budgeted_spent": "70.30",
        "budget_remaining": "99.70",
    }
    assert summary["other_currency_transactions"] == 1
    assert Decimal(summary["totals"]["expenses"]) == Decimal("0.10") + Decimal("0.20") + Decimal(
        "45"
    ) + Decimal("25") + Decimal("3.33")
    usd = (
        await client.get(f"/v1/finance/summary?space_id={space}&month=2026-09&currency=USD")
    ).json()
    assert usd["totals"]["expenses"] == "12.00"
    # No cloud involved at all (the space has no cloud consent).
    assert fake_llm.requests == []

    bad = await client.get(f"/v1/finance/summary?space_id={space}&month=2026-9")
    assert bad.status_code == 422
    unknown = await client.get(f"/v1/finance/summary?space_id={uuid.uuid4()}")
    assert unknown.status_code == 404


async def test_dashboard_budget_block(client: AsyncClient, spaces: dict[str, str]) -> None:
    space = spaces["personal"]
    assert (await client.get("/v1/dashboard")).json()["budget"] is None
    categories = await _defaults(client, space)
    today = _paris_today().isoformat()
    spending = {
        "Alimentation": ("100", "85"),
        "Restaurants": ("100", "90"),
        "Loisirs": ("100", "120"),
        "Transports": ("100", "95"),
        "Habillement": ("100", "50"),
    }
    for name, (budget, spent) in spending.items():
        await client.patch(
            f"/v1/finance/categories/{categories[name]}", json={"monthly_budget": budget}
        )
        await _tx(client, space, amount=spent, category_id=categories[name], tx_date=today)
    block = (await client.get("/v1/dashboard")).json()["budget"]
    assert block["space_id"] == space
    assert block["month"] == today[:7]
    assert block["spent"] == "440.00" and block["budget"] == "500.00"
    assert [(a["name"], a["ratio"]) for a in block["alerts"]] == [
        ("Loisirs", 1.2),
        ("Transports", 0.95),
        ("Restaurants", 0.9),
    ]
    # The selected space drives the block.
    pro = (await client.get(f"/v1/dashboard?space_id={spaces['professional']}")).json()
    assert pro["budget"] is None


# --- Receipts --------------------------------------------------------------------------------


async def test_receipt_proposal_then_owner_records_the_transaction(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, logged_in: Owner
) -> None:
    space = spaces["personal"]
    categories = await _defaults(client, space)
    pending = await _document(
        db, logged_in, space, title="ticket.jpg", status="processing", analysis=None
    )
    note = await _document(db, logged_in, space, title="note.jpg", purpose="document")
    ready = await _document(
        db, logged_in, space, title="ticket-carrefour.jpg", analysis=_receipt_analysis()
    )

    not_ready = await client.get(f"/v1/finance/receipts/{pending}/proposal")
    assert not_ready.status_code == 409
    assert not_ready.json()["code"] == "receipt_not_ready"
    assert not_ready.json()["status"] == 409
    assert not_ready.json()["document_status"] == "processing"
    not_receipt = await client.get(f"/v1/finance/receipts/{note}/proposal")
    assert not_receipt.status_code == 422 and not_receipt.json()["code"] == "not_a_receipt"
    missing = await client.get(f"/v1/finance/receipts/{uuid.uuid4()}/proposal")
    assert missing.status_code == 404

    proposal = (await client.get(f"/v1/finance/receipts/{ready}/proposal")).json()
    assert proposal["merchant"] == "CARREFOUR MARKET"
    assert proposal["tx_date"] == "2026-10-10"
    assert proposal["total"] == "6.43"
    assert proposal["vat_rates"] == ["5,5"]
    assert proposal["confidence"] >= 0.85
    assert proposal["lines"]["total"].startswith("TOTAL TTC")
    assert proposal["suggested_category"] == {
        "id": categories["Alimentation"],
        "name": "Alimentation",
        "source": "keyword",
    }
    assert proposal["recorded_transaction_ids"] == []
    # Nothing is recorded automatically from a receipt.
    assert (await client.get("/v1/finance/transactions")).json()["items"] == []

    # The owner corrects the values (here the category) and records it.
    recorded = await _tx(
        client,
        space,
        amount=proposal["total"],
        tx_date=proposal["tx_date"],
        merchant=proposal["merchant"],
        category_id=categories["Divers"],
        source_type="receipt",
        document_id=ready,
    )
    assert recorded["document_id"] == ready and recorded["source_type"] == "receipt"
    again = (await client.get(f"/v1/finance/receipts/{ready}/proposal")).json()
    assert again["recorded_transaction_ids"] == [recorded["id"]]
    # The owner's choice now beats the keyword heuristic.
    assert again["suggested_category"]["name"] == "Divers"
    assert again["suggested_category"]["source"] == "rule"


# --- Isolation -------------------------------------------------------------------------------


async def test_finances_are_isolated_by_owner_and_space(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal, pro = spaces["personal"], spaces["professional"]
    pro_categories = await _defaults(client, pro)
    pro_tx = await _tx(client, pro, merchant="Papeterie", category_id=pro_categories["Divers"])
    await _tx(client, personal, merchant="Primeur")
    listed = (await client.get(f"/v1/finance/transactions?space_id={personal}")).json()
    assert [t["merchant"] for t in listed["items"]] == ["Primeur"]

    # Another user sees nothing of it, even by id, and cannot write into these spaces.
    stranger = User(email="other@example.org", display_name="Other", role="owner")
    db.add(stranger)
    await db.flush()
    db.add(Space(user_id=stranger.id, kind="personal", name="Perso"))
    await db.commit()
    ctx = await make_ctx(database, db, settings, stranger, fake_llm)
    with pytest.raises(ProblemError) as caught:
        await finance.get_transaction(ctx, uuid.UUID(pro_tx["id"]))
    assert caught.value.code == "transaction_not_found"
    with pytest.raises(ProblemError) as caught:
        await finance.summary(ctx, space_id=uuid.UUID(pro))
    assert caught.value.code == "space_not_found"
    assert (await finance.list_transactions(ctx, space_id=None)).items == []
    assert await finance.list_categories(ctx, space_id=None) == []

    # An agent granted finances in the personal space only never sees the professional one.
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
    visible = await finance.list_transactions(agent_ctx, space_id=None)
    assert [t.merchant for t in visible.items] == ["Primeur"]
    with pytest.raises(ProblemError) as caught:
        await finance.get_transaction(agent_ctx, uuid.UUID(pro_tx["id"]))
    assert caught.value.status == 404
    # Reading is not writing: an agent needs the owner's approval to record anything.
    with pytest.raises(ProblemError) as caught:
        await finance.create_defaults(agent_ctx, uuid.UUID(personal))
    assert caught.value.code in ("approval_required", "action_denied")


# --- Finance agent (F08) ---------------------------------------------------------------------


async def test_finance_agent_needs_permissions_and_documents_need_their_own(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    pro = spaces["professional"]
    await enable_cloud(client, pro)
    categories = await _defaults(client, pro)
    await client.patch(
        f"/v1/finance/categories/{categories['Divers']}", json={"monthly_budget": "200"}
    )
    await _tx(
        client,
        pro,
        amount="42",
        merchant="Bureau Vallée",
        category_id=categories["Divers"],
        tx_date=_paris_today().isoformat(),
    )
    await _document(
        db,
        logged_in,
        pro,
        title="ticket-fournitures.jpg",
        analysis=_receipt_analysis(),
        text="LAIT DEMI ECREME 1L",
    )
    await _document(
        db,
        logged_in,
        pro,
        title="releve-confidentiel.pdf",
        analysis=_receipt_analysis(),
        sensitivity=["bank"],
        text="IBAN FR76 3000 6000 0112 3456 7890 189",
    )
    conversation = (await client.post("/v1/chat/conversations", json={"space_id": pro})).json()[
        "id"
    ]

    async def ask(content: str) -> dict[str, Any]:
        response = await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": content, "agent": "finance"},
        )
        assert response.status_code in (200, 201), response.text
        result: dict[str, Any] = response.json()
        return result

    agents = (await client.get(f"/v1/agents?space_id={pro}")).json()
    described = next(a for a in agents if a["name"] == "finance")
    assert described["label"] == "Finances"
    assert described["missing_permissions"] == ["finances"]
    assert described["missing_optional"] == ["documents"]  # works without receipts
    refused = await ask("Où en est mon budget ce mois-ci ?")
    assert refused["error"]["code"] == "agent_permission_required"
    assert refused["error"]["missing"] == ["finances", "documents"]
    assert fake_llm.requests == []

    # Finances only: the agent works, the professional receipts stay out of reach.
    granted = await client.post(
        "/v1/guardian/permissions",
        json={"agent": "finance", "space_id": pro, "resource": "finances", "purpose": "Budget"},
    )
    assert granted.status_code == 201, granted.text
    answered = await ask("Où en est mon budget ce mois-ci ?")
    assert answered["error"] is None, answered
    system = fake_llm.requests[-1].system or ""
    assert fake_llm.requests[-1].purpose == "agent.finance"
    assert "BUDGET DU MOIS" in system
    assert "Divers : 42,00 € dépensés / budget 200,00 € (21 %)" in system
    assert "Bureau Vallée" in system
    assert "Tu ne déplaces jamais d'argent" in system
    assert "ticket-fournitures" not in system
    assert "TICKETS DE CAISSE" not in system

    # Documents granted in this space: receipts (merchant and totals) become visible.
    await client.post(
        "/v1/guardian/permissions",
        json={"agent": "finance", "space_id": pro, "resource": "documents", "purpose": "Tickets"},
    )
    await ask("Et mes tickets ?")
    system = fake_llm.requests[-1].system or ""
    # The merchant read on the receipt, never the uploaded file name.
    assert "ticket-fournitures.jpg" not in system
    assert "CARREFOUR MARKET (total lu 6,43 €, daté du 10/10/2026" in system
    # Never the extracted text, and never a sensitive document.
    assert "LAIT DEMI ECREME" not in system
    assert "releve-confidentiel" not in system and "FR76" not in system
