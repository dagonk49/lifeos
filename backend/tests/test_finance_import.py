"""Bank statement CSV imports (task 25): exact parsing, preview without any write,
validation without duplicates, undo, limits and isolation."""

import asyncio
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.finance import csv_import, imports
from lifeos.modules.finance.csv_import import CellError, CsvError
from lifeos.modules.finance.models import (
    CategoryRule,
    FinanceImport,
    FinanceImportRow,
    Transaction,
)
from lifeos.modules.finance.schemas import ImportCommit, ImportUploadFields
from lifeos.modules.guardian.principal import agent_principal
from lifeos.modules.identity.models import Space, User
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.privacy import erasure
from lifeos.workers.scheduler import build_scheduler

from .conftest import Owner, make_ctx

FIXTURES = Path(__file__).parent / "fixtures" / "finance_csv"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- Pure parsing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "separator", "expected"),
    [
        ("1 234,56", ",", "1234.56"),
        ("1.234,56", ",", "1234.56"),
        ("-12,50", ",", "-12.50"),
        (chr(0x2212) + "12,50", ",", "-12.50"),
        ("12,5", ",", "12.50"),
        ("12-", ",", "-12.00"),
        ("+3", ",", "3.00"),
        ("12,50 €", ",", "12.50"),
        ("EUR 3,00", ",", "3.00"),
        ("-7,10 EUR", ",", "-7.10"),
        ("1" + chr(0xA0) + "234,56", ",", "1234.56"),
        ("1" + chr(0x202F) + "234,56", ",", "1234.56"),
        ("1,234.56", ".", "1234.56"),
        ("-18.90", ".", "-18.90"),
        ("0,00", ",", "0.00"),
    ],
)
def test_parse_csv_amount_is_exact(cell: str, separator: str, expected: str) -> None:
    amount = csv_import.parse_csv_amount(cell, separator)
    assert amount == Decimal(expected)
    assert str(amount) == expected


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        ("12,345", "amount_precision"),
        ("1234567890123,00", "amount_too_large"),
        ("abc", "amount_invalid"),
        ("12,50,3", "amount_invalid"),
        ("1.234.5", "amount_invalid"),
        ("€", "amount_invalid"),
    ],
)
def test_parse_csv_amount_refuses_rather_than_rounds(cell: str, reason: str) -> None:
    with pytest.raises(CellError) as caught:
        csv_import.parse_csv_amount(cell, ",")
    assert caught.value.reason == reason
    assert csv_import.parse_csv_amount("  ", ",") is None


def test_parse_date_formats() -> None:
    assert csv_import.parse_date("05/09/2026", "DD/MM/YYYY") == date(2026, 9, 5)
    assert csv_import.parse_date("5/9/2026 00:00", "DD/MM/YYYY") == date(2026, 9, 5)
    assert csv_import.parse_date("05/09/26", "DD/MM/YY") == date(2026, 9, 5)
    assert csv_import.parse_date("2026-09-05", "YYYY-MM-DD") == date(2026, 9, 5)
    assert csv_import.parse_date("05.09.2026", "DD.MM.YYYY") == date(2026, 9, 5)
    for cell, fmt in (("31/02/2026", "DD/MM/YYYY"), ("2026-09-05", "DD/MM/YYYY"), ("", "DD/MM/YY")):
        with pytest.raises(CellError):
            csv_import.parse_date(cell, fmt)
    assert csv_import.guess_date_format(["2026-09-05", "2026-10-01"]) == "YYYY-MM-DD"
    assert csv_import.guess_decimal_separator(["-18.90", "1,234.00"]) == "."
    assert csv_import.guess_decimal_separator(["-18,90", "1.234,00"]) == ","


def test_decoding_delimiter_and_header_skip_the_preamble() -> None:
    text, encoding = csv_import.decode(_fixture("signed_cp1252.csv"))
    assert encoding == "cp1252"
    assert "Libellé" in text  # 0xE9 read as « é »
    parsed = csv_import.parse_file(text)
    assert parsed.delimiter == ";"
    assert parsed.header == ["Date", "Libellé", "Montant"]
    assert parsed.preamble_lines == 4
    # The preamble (account number, holder, balance) is never kept.
    assert all("00012345678" not in " ".join(cells) for _, cells in parsed.rows)
    assert [line for line, _ in parsed.rows] == [6, 7, 8, 9, 10]

    text, encoding = csv_import.decode(_fixture("debit_credit.csv"))
    assert encoding == "utf-8"
    assert not text.startswith("﻿")
    assert csv_import.detect_delimiter(text) == ";"
    text, _ = csv_import.decode(_fixture("value_date_reference.csv"))
    assert csv_import.detect_delimiter(text) == ","

    # A forced encoding that does not fit is refused, as are binaries and web pages.
    with pytest.raises(CsvError) as caught:
        csv_import.decode("Libellé".encode("cp1252"), "utf-8")
    assert caught.value.code == "unsupported_encoding"
    for data in (b"\x89PNG\r\n\x1a\n\x00\x00", b"<html><body>x</body></html>", b"\x01\x02" * 50):
        with pytest.raises(CsvError):
            csv_import.decode(data)


def test_suggested_mappings_for_three_layouts() -> None:
    def suggest(name: str) -> csv_import.Mapping:
        text, _ = csv_import.decode(_fixture(name))
        parsed = csv_import.parse_file(text)
        mapping = csv_import.suggest_mapping(parsed.header, [c for _, c in parsed.rows])
        assert mapping is not None
        return mapping

    signed = suggest("signed_cp1252.csv")
    assert (signed.date_column, signed.label_column, signed.amount_column) == (0, 1, 2)
    assert (signed.amount_mode, signed.date_format, signed.decimal_separator) == (
        "signed",
        "DD/MM/YYYY",
        ",",
    )
    debit = suggest("debit_credit.csv")
    # The operation date, not the value date.
    assert (debit.date_column, debit.label_column) == (0, 2)
    assert (debit.amount_mode, debit.debit_column, debit.credit_column) == ("debit_credit", 3, 4)
    value = suggest("value_date_reference.csv")
    assert (value.date_column, value.label_column, value.amount_column) == (1, 2, 3)
    assert (value.reference_column, value.date_format, value.decimal_separator) == (
        4,
        "YYYY-MM-DD",
        ".",
    )


def test_bank_labels_and_import_keys() -> None:
    assert csv_import.clean_bank_label("CB CARREFOUR MARKET 31/08") == "CARREFOUR MARKET"
    assert csv_import.clean_bank_label("PRLV SEPA EDF") == "EDF"
    assert (
        csv_import.clean_bank_label("PAIEMENT PAR CARTE X1234 BOULANGERIE DU MARCHE 02/09")
        == "BOULANGERIE DU MARCHE"
    )
    assert csv_import.clean_bank_label("CB 12/10") is None

    base: dict[str, Any] = {
        "account": "Compte joint",
        "tx_date": date(2026, 9, 2),
        "direction": "expense",
        "amount": Decimal("2.50"),
        "currency": "EUR",
        "label": "CB CAFE DU COIN 02/09",
        "reference": None,
    }
    first = csv_import.import_key(**base, occurrence=0)
    assert first == csv_import.import_key(
        **{**base, "label": "cb  café du coin 02/09"}, occurrence=0
    )
    # Two identical coffees the same day stay two operations.
    assert first != csv_import.import_key(**base, occurrence=1)
    assert first != csv_import.import_key(**{**base, "account": "Livret"}, occurrence=0)
    # A bank reference identifies the operation by itself.
    with_ref = {**base, "reference": "REF1"}
    assert csv_import.import_key(**with_ref, occurrence=0) == csv_import.import_key(
        **with_ref, occurrence=3
    )


# --- API -----------------------------------------------------------------------------------


async def _upload(
    client: AsyncClient,
    space: str,
    data: bytes,
    *,
    name: str = "releve.csv",
    key: str | None = None,
    **fields: str,
) -> Any:
    headers = {"Idempotency-Key": key} if key else {}
    return await client.post(
        "/v1/finance/imports",
        data={"space_id": space, **fields},
        files={"file": (name, data, "text/csv")},
        headers=headers,
    )


async def _rows(client: AsyncClient, import_id: str, **params: str) -> list[dict[str, Any]]:
    response = await client.get(f"/v1/finance/imports/{import_id}/rows", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def _count(db: AsyncSession, *conditions: Any) -> int:
    return int(
        await db.scalar(select(func.count()).select_from(Transaction).where(*conditions)) or 0
    )


def test_lifeos_export_is_recognised() -> None:
    """The archive's `transactions.csv` (comma, ISO dates, decimal point) maps by itself."""
    text = (
        "id,space,date,direction,amount,signed_amount,currency,merchant,label,category,"
        "category_kind,category_confirmed,notes,source_type,document_id,created_at,updated_at\r\n"
        "6f1c,Perso,2026-09-01,expense,45.30,-45.30,EUR,Carrefour,'-10 % CB CARREFOUR,"
        "Alimentation,expense,true,,manual,,2026-09-01T10:00:00+02:00,2026-09-01T10:00:00+02:00\r\n"
    )
    decoded, _ = csv_import.decode(b"\xef\xbb\xbf" + text.encode("utf-8"))
    parsed = csv_import.parse_file(decoded)
    assert parsed.delimiter == ","
    mapping = csv_import.suggest_mapping(parsed.header, [c for _, c in parsed.rows])
    assert mapping is not None
    assert (mapping.date_column, mapping.label_column, mapping.amount_column) == (2, 8, 5)
    assert (mapping.reference_column, mapping.currency_column) == (0, 6)
    assert (mapping.date_format, mapping.decimal_separator) == ("YYYY-MM-DD", ".")
    line, cells = parsed.rows[0]
    reading = csv_import.read_row(line, cells, mapping, len(parsed.header))
    assert (reading.direction, reading.amount, reading.label) == (
        "expense",
        Decimal("45.30"),
        "-10 % CB CARREFOUR",
    )


async def _learn_carrefour(client: AsyncClient, space: str) -> tuple[str, str]:
    categories = (
        await client.post("/v1/finance/categories/defaults", json={"space_id": space})
    ).json()
    food = next(c["id"] for c in categories if c["name"] == "Alimentation")
    manual = await client.post(
        "/v1/finance/transactions",
        json={
            "space_id": space,
            "amount": "12.00",
            "tx_date": "2026-08-10",
            "merchant": "Carrefour Market",
            "category_id": food,
        },
    )
    assert manual.status_code == 201, manual.text
    return food, manual.json()["id"]


async def test_preview_writes_nothing_then_commit_does_not_duplicate(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    food, _ = await _learn_carrefour(client, personal)
    hits_before = await db.scalar(select(CategoryRule.hits))
    data = _fixture("signed_cp1252.csv")

    created = await _upload(client, personal, data, account_label="Compte courant")
    assert created.status_code == 201, created.text
    preview = created.json()
    assert preview["status"] == "preview"
    assert preview["encoding"] == "cp1252"
    assert preview["header"] == ["Date", "Libellé", "Montant"]
    assert preview["row_count"] == 5
    assert preview["sample"][0] == ["01/09/2026", "CB CARREFOUR MARKET 31/08", "-45,30"]
    # The suggested mapping is applied at once (still read only).
    assert preview["mapping"]["amount_column"] == 2
    assert preview["counts"] == {"valid": 5}
    assert preview["totals"] == {"expenses": "1284.86", "income": "2000.00", "other_currency": 0}
    rows = await _rows(client, preview["id"])
    carrefour = rows[0]
    assert carrefour["merchant"] == "CARREFOUR MARKET"
    assert carrefour["amount"] == "45.30"
    assert carrefour["direction"] == "expense"
    assert carrefour["category_id"] == food
    assert rows[4]["amount"] == "1234.56"

    # « Terminé quand » 2: nothing is written before the owner validates.
    remapped = await client.put(
        f"/v1/finance/imports/{preview['id']}/mapping", json=preview["mapping"]
    )
    assert remapped.status_code == 200, remapped.text
    db.expire_all()
    assert await _count(db) == 1  # the manual entry only
    assert await db.scalar(select(CategoryRule.hits)) == hits_before

    committed = await client.post(
        f"/v1/finance/imports/{preview['id']}/commit", json={}, headers={"Idempotency-Key": "c-1"}
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["status"] == "committed"
    assert (result["imported_count"], result["skipped_count"]) == (5, 0)
    imported = list(
        await db.scalars(select(Transaction).where(Transaction.source_type == "import"))
    )
    assert len(imported) == 5
    by_merchant = {t.merchant: t for t in imported}
    assert by_merchant["CARREFOUR MARKET"].category_id == uuid.UUID(food)
    assert by_merchant["CARREFOUR MARKET"].category_confirmed is False
    assert by_merchant["CARREFOUR MARKET"].label == "CB CARREFOUR MARKET 31/08"
    assert sorted(str(t.amount) for t in imported) == [
        "1234.56",
        "2.50",
        "2.50",
        "2000.00",
        "45.30",
    ]
    db.expire_all()
    assert await db.scalar(select(CategoryRule.hits)) == (hits_before or 0) + 1
    # The preview cells are erased once validated.
    assert await db.scalar(select(func.count()).select_from(FinanceImportRow)) == 0

    # Replaying the validation returns the same answer; a new one is refused.
    replay = await client.post(
        f"/v1/finance/imports/{preview['id']}/commit", json={}, headers={"Idempotency-Key": "c-1"}
    )
    assert replay.json() == result
    again = await client.post(f"/v1/finance/imports/{preview['id']}/commit", json={})
    assert again.status_code == 409
    assert again.json()["code"] == "import_not_in_preview"

    # « Terminé quand » 1: the same file again adds nothing.
    second = (await _upload(client, personal, data, account_label="Compte courant")).json()
    assert second["already_imported_file"] is True
    assert second["counts"] == {"already_imported": 5}
    assert second["to_import"] == 0
    done = (await client.post(f"/v1/finance/imports/{second['id']}/commit", json={})).json()
    assert done["imported_count"] == 0
    db.expire_all()
    assert await _count(db, Transaction.source_type == "import") == 5


async def test_overlapping_file_only_adds_new_rows(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    first = "Date;Libellé;Montant\n02/09/2026;CB CAFE;-2,50\n03/09/2026;CB KIOSQUE;-1,20\n"
    created = (await _upload(client, personal, first.encode())).json()
    await client.post(f"/v1/finance/imports/{created['id']}/commit", json={})
    overlap = first + "04/09/2026;CB CAFE;-2,50\n04/09/2026;CB CAFE;-2,50\n"
    second = (await _upload(client, personal, overlap.encode())).json()
    assert second["counts"] == {"already_imported": 2, "valid": 2}
    done = (await client.post(f"/v1/finance/imports/{second['id']}/commit", json={})).json()
    assert done["imported_count"] == 2
    assert await _count(db, Transaction.source_type == "import") == 4

    # The same rows imported into another space are not duplicates there.
    other = (await _upload(client, spaces["professional"], first.encode())).json()
    assert other["counts"] == {"valid": 2}


async def test_possible_duplicates_exclusions_and_invalid_rows(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    ticket = await client.post(
        "/v1/finance/transactions",
        json={"space_id": personal, "amount": "45.30", "tx_date": "2026-08-31", "merchant": "X"},
    )
    assert ticket.status_code == 201
    future = (date.today() + timedelta(days=400)).strftime("%d/%m/%Y")
    text = (
        "Date;Libellé;Montant\n"
        "01/09/2026;CB CARREFOUR MARKET;-45,30\n"
        "02/09/2026;CB CAFE;-2,505\n"
        "31/02/2026;CB ERREUR;-1,00\n"
        f"{future};CB DEMAIN;-3,00\n"
        "03/09/2026;CB RIEN;0,00\n"
        "04/09/2026;CB GARDE;-9,00\n"
        "05/09/2026;CB EXCLUE;-8,00\n"
        "Solde final;;\n"
    )
    preview = (await _upload(client, personal, text.encode())).json()
    assert preview["counts"] == {"possible_duplicate": 1, "invalid": 5, "valid": 2}
    rows = {r["line_no"]: r for r in await _rows(client, preview["id"])}
    assert rows[2]["status"] == "possible_duplicate"
    assert rows[2]["match_id"] == ticket.json()["id"]
    assert rows[2]["match_date"] == "2026-08-31"
    assert [rows[n]["reason"] for n in (3, 4, 5, 6, 9)] == [
        "amount_precision",
        "date_invalid",
        "date_in_future",
        "amount_zero",
        "date_invalid",  # a footer line ("Solde final") is shown, never imported
    ]
    assert rows[3]["cells"] == ["02/09/2026", "CB CAFE", "-2,505"]
    invalid = await _rows(client, preview["id"], status="invalid")
    assert len(invalid) == 5

    # The possible duplicate stays out unless included; a valid row can be left out.
    done = (
        await client.post(
            f"/v1/finance/imports/{preview['id']}/commit", json={"exclude_lines": [8]}
        )
    ).json()
    assert (done["imported_count"], done["skipped_count"]) == (1, 7)
    merchants = set(
        await db.scalars(select(Transaction.merchant).where(Transaction.source_type == "import"))
    )
    assert merchants == {"GARDE"}

    again = (await _upload(client, personal, text.encode())).json()
    assert again["counts"]["already_imported"] == 1
    included = (
        await client.post(f"/v1/finance/imports/{again['id']}/commit", json={"include_lines": [2]})
    ).json()
    assert included["imported_count"] == 2  # the possible duplicate and the line left out


async def test_debit_credit_mapping_and_mapping_errors(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    preview = (await _upload(client, spaces["personal"], _fixture("debit_credit.csv"))).json()
    assert preview["encoding"] == "utf-8"
    assert preview["totals"] == {"expenses": "63.20", "income": "25.00", "other_currency": 0}
    rows = await _rows(client, preview["id"])
    assert [(r["direction"], r["amount"], r["tx_date"]) for r in rows] == [
        ("expense", "3.20", "2026-09-03"),
        ("income", "25.00", "2026-09-07"),
        ("expense", "60.00", "2026-09-08"),
    ]
    # The value date instead of the operation date: the owner's choice.
    mapping = {**preview["mapping"], "date_column": 1}
    remapped = (
        await client.put(f"/v1/finance/imports/{preview['id']}/mapping", json=mapping)
    ).json()
    assert remapped["mapping"]["date_column"] == 1
    assert (await _rows(client, preview["id"]))[0]["tx_date"] == "2026-09-04"

    wrong = await client.put(
        f"/v1/finance/imports/{preview['id']}/mapping", json={**mapping, "label_column": 9}
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "mapping_invalid"
    incomplete = await client.put(
        f"/v1/finance/imports/{preview['id']}/mapping",
        json={"date_column": 0, "label_column": 2, "amount_mode": "signed"},
    )
    assert incomplete.status_code == 422


async def test_undo_removes_only_this_import_and_reports_edits(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    await _learn_carrefour(client, personal)
    other = "Date;Libellé;Montant\n20/09/2026;CB AUTRE;-4,00\n"
    kept = (await _upload(client, personal, other.encode())).json()
    await client.post(f"/v1/finance/imports/{kept['id']}/commit", json={})
    preview = (await _upload(client, personal, _fixture("signed_cp1252.csv"))).json()
    await client.post(f"/v1/finance/imports/{preview['id']}/commit", json={})
    edited = await db.scalar(
        select(Transaction.id).where(Transaction.import_id == uuid.UUID(preview["id"])).limit(1)
    )
    patched = await client.patch(f"/v1/finance/transactions/{edited}", json={"notes": "vérifié"})
    assert patched.status_code == 200

    refused = await client.post(f"/v1/finance/imports/{preview['id']}/undo", json={})
    assert refused.status_code == 422
    undone = await client.post(f"/v1/finance/imports/{preview['id']}/undo", json={"confirm": True})
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    assert undone.json()["edited_count"] == 1
    db.expire_all()
    assert await _count(db, Transaction.import_id == uuid.UUID(preview["id"])) == 0
    assert await _count(db, Transaction.source_type == "import") == 1
    assert await _count(db, Transaction.source_type == "manual") == 1
    twice = await client.post(f"/v1/finance/imports/{preview['id']}/undo", json={"confirm": True})
    assert twice.json()["code"] == "import_not_committed"

    # Imported again later: the operations come back.
    again = (await _upload(client, personal, _fixture("signed_cp1252.csv"))).json()
    assert again["counts"] == {"valid": 5}
    assert again["already_imported_file"] is False

    # Cancelling a preview leaves nothing behind.
    discarded = await client.delete(f"/v1/finance/imports/{again['id']}")
    assert discarded.status_code == 204
    assert await db.get(FinanceImport, uuid.UUID(again["id"])) is None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(FinanceImportRow)
            .where(FinanceImportRow.import_id == uuid.UUID(again["id"]))
        )
        == 0
    )
    history = (await client.get(f"/v1/finance/imports?space_id={personal}")).json()
    assert [h["status"] for h in history] == ["undone", "committed"]


async def test_undo_is_replayed_after_a_restore(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, logged_in: Owner
) -> None:
    preview = (await _upload(client, spaces["personal"], _fixture("debit_credit.csv"))).json()
    await client.post(f"/v1/finance/imports/{preview['id']}/commit", json={})
    lines = [
        ",".join(erasure.EXPORT_COLUMNS),
        f"2026-10-25T10:00:00Z,{logged_in.user.id},finance.import.undo,finance_import,{preview['id']}",
    ]
    replayed = await erasure.replay(db, erasure.parse_erasures(lines), apply=True)
    await db.commit()
    assert replayed.erased["finance.import.undo"] == 1
    assert await _count(db, Transaction.source_type == "import") == 0


async def test_concurrent_commits_do_not_double(
    database: Database,
    settings: Settings,
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    preview = (await _upload(client, spaces["personal"], _fixture("debit_credit.csv"))).json()
    import_id = uuid.UUID(preview["id"])

    async def commit() -> str:
        async with database.sessionmaker() as session, session.begin():
            user = await session.get(User, logged_in.user.id)
            assert user is not None
            ctx = await make_ctx(database, session, settings, user, fake_llm)
            try:
                await imports.commit_import(ctx, import_id, ImportCommit())
            except ProblemError as error:
                return error.code
            return "ok"

    results = await asyncio.gather(commit(), commit())
    assert sorted(results) == ["import_not_in_preview", "ok"]
    async with database.sessionmaker() as session:
        assert await _count(session, Transaction.import_id == import_id) == 3


async def test_limits_types_and_expiry(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, settings: Settings
) -> None:
    personal = spaces["personal"]
    too_big = b"Date;Libelle;Montant\n" + b"01/09/2026;CB X;-1,00\n" * 120_000
    assert (await _upload(client, personal, too_big)).json()["code"] == "document_too_large"
    binary = await _upload(client, personal, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert binary.status_code == 415
    assert binary.json()["code"] == "import_unreadable"
    html = await _upload(client, personal, b"<!doctype html><html><body>x</body></html>")
    assert html.status_code == 415
    rows = b"Date;Libelle;Montant\n" + b"01/09/2026;CB X;-1,00\n" * (
        settings.finance_import_max_rows + 1
    )
    too_many = await _upload(client, personal, rows)
    assert too_many.status_code == 422
    assert too_many.json()["code"] == "too_many_rows"
    empty = await _upload(client, personal, b"Date;Libelle;Montant\n")
    assert empty.json()["code"] == "import_empty"
    db.expire_all()
    assert await db.scalar(select(func.count()).select_from(FinanceImport)) == 0

    # Idempotent upload: a retry returns the same preview, another file is refused.
    data = _fixture("debit_credit.csv")
    first = await _upload(client, personal, data, key="u-1")
    retry = await _upload(client, personal, data, key="u-1")
    assert retry.json()["id"] == first.json()["id"]
    other = await _upload(client, personal, _fixture("signed_cp1252.csv"), key="u-1")
    assert other.json()["code"] == "idempotency_key_reused"

    # An expired preview cannot be validated, and is purged.
    import_id = uuid.UUID(first.json()["id"])
    await db.execute(
        update(FinanceImport)
        .where(FinanceImport.id == import_id)
        .values(expires_at=FinanceImport.created_at - timedelta(hours=1))
    )
    await db.commit()
    late = await client.post(f"/v1/finance/imports/{import_id}/commit", json={})
    assert late.json()["code"] == "import_expired"
    assert await imports.purge_expired_imports(db) == 1
    await db.commit()
    assert await db.get(FinanceImport, import_id) is None
    assert "purge_finance_imports" in {job.id for job in build_scheduler().get_jobs()}


async def test_isolation_agents_and_audit_without_content(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    preview = (await _upload(client, personal, _fixture("signed_cp1252.csv"))).json()
    await client.post(f"/v1/finance/imports/{preview['id']}/commit", json={})
    import_id = uuid.UUID(preview["id"])

    stranger = User(email="other@example.org", display_name="Other", role="owner")
    db.add(stranger)
    await db.flush()
    db.add(Space(user_id=stranger.id, kind="personal", name="Perso"))
    await db.commit()
    ctx = await make_ctx(database, db, settings, stranger, fake_llm)
    for call in (imports.get_import(ctx, import_id), imports.undo_import(ctx, import_id)):
        with pytest.raises(ProblemError) as caught:
            await call
        assert caught.value.status == 404
    with pytest.raises(ProblemError) as caught:
        await imports.create_import(
            ctx,
            ImportUploadFields(space_id=uuid.UUID(personal)),
            data=b"a;b\n1;2\n",
            filename=None,
        )
    assert caught.value.status == 404

    # A finance agent, even granted the finances, never imports nor undoes.
    await client.post(
        "/v1/guardian/permissions",
        json={
            "agent": "finance",
            "space_id": personal,
            "resource": "finances",
            "access": "write",
            "purpose": "Assistant Finances",
        },
    )
    owner = await db.get(User, logged_in.user.id)
    assert owner is not None
    agent = await agent_principal(db, owner.id, "finance")
    agent_ctx = await make_ctx(database, db, settings, owner, fake_llm, principal=agent)
    with pytest.raises(ProblemError) as caught:
        await imports.create_import(
            agent_ctx,
            ImportUploadFields(space_id=uuid.UUID(personal)),
            data=_fixture("debit_credit.csv"),
            filename=None,
        )
    assert caught.value.code == "action_denied"
    assert caught.value.extra.get("reason") == "forbidden_for_agents"
    with pytest.raises(ProblemError) as caught:
        await imports.undo_import(agent_ctx, import_id)
    assert caught.value.extra.get("reason") == "forbidden_for_agents"
    await db.rollback()

    events = list(
        await db.scalars(select(AuditEvent).where(AuditEvent.action.like("finance.import.%")))
    )
    assert {e.action for e in events} >= {"finance.import.create", "finance.import.commit"}
    dumped = json.dumps([e.details for e in events], default=str)
    for content in ("CARREFOUR", "SALAIRE", "45.30", "1234.56", "2000", "00012345678"):
        assert content not in dumped

    exported = (await client.get("/v1/privacy/export")).json()
    assert [i["id"] for i in exported["finance_imports"]] == [preview["id"]]
