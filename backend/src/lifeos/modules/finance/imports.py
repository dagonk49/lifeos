"""Bank statement imports (F04, task 25): CSV file -> preview -> owner's validation.

Nothing is written to the budget before the owner validates: the upload stores the parsed
rows of a preview only (`finance_import_rows`, erased at validation, cancellation or after
`finance_import_preview_hours`), and reading them is side-effect free (no rule hit, no
transaction). Each imported operation carries a stable `import_key`; a unique index per
space makes a second import of the same operation impossible, even for two concurrent
validations. An import can be undone as a whole (named confirmation in the interface).

Agents never import (`finance.import` has no agent level). Bank connections are a
separate project: only files the owner downloaded from their bank are read here.
"""

import hashlib
import uuid
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import MAX_LIMIT, Page
from lifeos.core.security import utcnow
from lifeos.modules.finance import csv_import
from lifeos.modules.finance.models import (
    CategoryRule,
    FinanceCategory,
    FinanceImport,
    FinanceImportRow,
    Transaction,
)
from lifeos.modules.finance.schemas import (
    ImportCommit,
    ImportMapping,
    ImportOut,
    ImportRowOut,
    ImportTotals,
    ImportUploadFields,
)
from lifeos.modules.finance.service import _audit, _q, _readable, local_today, merchant_key
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.privacy import erasure

# A bank operation and a ticket or a manual entry of the same amount within this many
# days are probably the same purchase (flagged, unchecked by default).
DUPLICATE_WINDOW = timedelta(days=3)
SAMPLE_ROWS = 5
_CHUNK = 1000
_ZERO = Decimal("0.00")

_CSV_ERRORS = {
    "import_unreadable": (415, "The file is not a readable CSV statement"),
    "unsupported_encoding": (422, "The file cannot be decoded with this encoding"),
    "import_empty": (422, "The file has no data rows"),
}


def _csv_problem(error: csv_import.CsvError) -> ProblemError:
    status, title = _CSV_ERRORS.get(error.code, (422, "The file cannot be read"))
    return ProblemError(status, error.code, title)


def _mapping_of(value: ImportMapping) -> csv_import.Mapping:
    return csv_import.Mapping(**value.model_dump())


def _as_schema(value: csv_import.Mapping | None) -> ImportMapping | None:
    if value is None:
        return None
    return ImportMapping.model_validate(value.__dict__)


# --- Reading ---------------------------------------------------------------------------------


async def get_import(ctx: Ctx, import_id: uuid.UUID, *, lock: bool = False) -> FinanceImport:
    stmt = select(FinanceImport).where(
        FinanceImport.id == import_id,
        FinanceImport.user_id == ctx.principal.user_id,
        FinanceImport.space_id.in_(_readable(ctx, None)),
    )
    found = await ctx.db.scalar(stmt.with_for_update() if lock else stmt)
    if found is None:
        raise not_found("import_not_found", "Import not found")
    return found


async def describe(ctx: Ctx, finance_import: FinanceImport) -> ImportOut:
    counts: dict[str, int] = {}
    sample: list[list[str]] = []
    totals: ImportTotals | None = None
    suggested: ImportMapping | None = None
    if finance_import.status == "preview":
        of_import = FinanceImportRow.import_id == finance_import.id
        for status, count in await ctx.db.execute(
            select(FinanceImportRow.status, func.count())
            .where(of_import)
            .group_by(FinanceImportRow.status)
        ):
            counts[status] = int(count)
        sample = list(
            await ctx.db.scalars(
                select(FinanceImportRow.cells)
                .where(of_import)
                .order_by(FinanceImportRow.line_no)
                .limit(SAMPLE_ROWS)
            )
        )
        suggested = _as_schema(csv_import.suggest_mapping(finance_import.header, sample))
        if finance_import.mapping is not None:
            totals = _totals(
                await ctx.db.scalars(
                    select(FinanceImportRow.parsed).where(
                        of_import, FinanceImportRow.status == "valid"
                    )
                ),
                (finance_import.mapping or {}).get("currency", "EUR"),
            )
    already = await ctx.db.scalar(
        select(FinanceImport.id)
        .where(
            FinanceImport.user_id == finance_import.user_id,
            FinanceImport.space_id == finance_import.space_id,
            FinanceImport.file_sha256 == finance_import.file_sha256,
            FinanceImport.status == "committed",
            FinanceImport.id != finance_import.id,
        )
        .limit(1)
    )
    return ImportOut(
        id=finance_import.id,
        space_id=finance_import.space_id,
        status=finance_import.status,
        filename=finance_import.filename,
        account_label=finance_import.account_label,
        encoding=finance_import.encoding,
        delimiter=finance_import.delimiter,
        header=finance_import.header,
        sample=sample,
        suggested_mapping=suggested,
        mapping=(
            ImportMapping.model_validate(finance_import.mapping)
            if finance_import.mapping is not None
            else None
        ),
        row_count=finance_import.row_count,
        counts=counts,
        to_import=counts.get("valid", 0),
        totals=totals,
        imported_count=finance_import.imported_count,
        skipped_count=finance_import.skipped_count,
        edited_count=finance_import.edited_count,
        already_imported_file=already is not None,
        expires_at=finance_import.expires_at,
        committed_at=finance_import.committed_at,
        undone_at=finance_import.undone_at,
        version=finance_import.version,
        created_at=finance_import.created_at,
    )


def _totals(parsed_rows: Any, currency: str) -> ImportTotals:
    expenses = income = _ZERO
    other = 0
    for parsed in parsed_rows:
        if not parsed:
            continue
        if parsed.get("currency") != currency:
            other += 1
            continue
        amount = Decimal(parsed["amount"])
        if parsed.get("direction") == "expense":
            expenses += amount
        else:
            income += amount
    return ImportTotals(expenses=_q(expenses), income=_q(income), other_currency=other)


async def list_imports(ctx: Ctx, *, space_id: uuid.UUID | None) -> list[ImportOut]:
    spaces = _readable(ctx, space_id)
    rows = await ctx.db.scalars(
        select(FinanceImport)
        .where(FinanceImport.user_id == ctx.principal.user_id, FinanceImport.space_id.in_(spaces))
        .order_by(FinanceImport.created_at.desc())
        .limit(50)
    )
    return [await describe(ctx, r) for r in rows]


def _row_out(row: FinanceImportRow) -> ImportRowOut:
    parsed = row.parsed or {}
    category = parsed.get("category_id")
    return ImportRowOut(
        line_no=row.line_no,
        status=row.status,
        reason=row.reason,
        cells=row.cells,
        tx_date=date.fromisoformat(parsed["tx_date"]) if parsed.get("tx_date") else None,
        amount=Decimal(parsed["amount"]) if parsed.get("amount") else None,
        direction=parsed.get("direction"),
        currency=parsed.get("currency"),
        label=parsed.get("label"),
        merchant=parsed.get("merchant"),
        category_id=uuid.UUID(category) if category else None,
        match_id=row.match_id,
        match_date=date.fromisoformat(parsed["match_date"]) if parsed.get("match_date") else None,
    )


async def list_rows(
    ctx: Ctx,
    import_id: uuid.UUID,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> Page[ImportRowOut]:
    """Rows of a preview in file order; the cursor is the last line number seen."""
    finance_import = await get_import(ctx, import_id)
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(FinanceImportRow).where(FinanceImportRow.import_id == finance_import.id)
    if status:
        stmt = stmt.where(FinanceImportRow.status == status)
    if cursor:
        if not cursor.isdigit():
            raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor")
        stmt = stmt.where(FinanceImportRow.line_no > int(cursor))
    rows = list(await ctx.db.scalars(stmt.order_by(FinanceImportRow.line_no).limit(limit + 1)))
    items = rows[:limit]
    return Page[ImportRowOut](
        items=[_row_out(r) for r in items],
        next_cursor=str(items[-1].line_no) if len(rows) > limit else None,
    )


# --- Upload and mapping (read-only towards the budget) ---------------------------------------


async def create_import(
    ctx: Ctx, fields: ImportUploadFields, *, data: bytes, filename: str | None
) -> FinanceImport:
    """Reads the file and stores its rows as a preview; the suggested mapping is applied
    (read only). Nothing reaches the budget."""
    ctx.principal.require_space(fields.space_id)
    await authorize(ctx, "finance.import", fields.space_id)
    try:
        text, encoding = csv_import.decode(
            data, None if fields.encoding == "auto" else fields.encoding
        )
        parsed = csv_import.parse_file(text)
    except csv_import.CsvError as error:
        raise _csv_problem(error) from error
    maximum = ctx.settings.finance_import_max_rows
    if not parsed.rows:
        raise ProblemError(422, "import_empty", "The file has no data rows")
    if len(parsed.rows) > maximum:
        raise ProblemError(
            422, "too_many_rows", "The file has too many rows", extra={"max_rows": maximum}
        )
    clipped = " ".join((filename or "").split())[:120] or None
    finance_import = FinanceImport(
        user_id=ctx.principal.user_id,
        space_id=fields.space_id,
        filename=clipped,
        file_sha256=hashlib.sha256(data).hexdigest(),
        account_label=fields.account_label,
        encoding=encoding,
        delimiter=parsed.delimiter,
        header=parsed.header,
        row_count=len(parsed.rows),
        expires_at=utcnow() + timedelta(hours=ctx.settings.finance_import_preview_hours),
    )
    ctx.db.add(finance_import)
    await ctx.db.flush()
    values = [
        {"import_id": finance_import.id, "line_no": line_no, "cells": cells, "status": "pending"}
        for line_no, cells in parsed.rows
    ]
    for start in range(0, len(values), _CHUNK):
        await ctx.db.execute(insert(FinanceImportRow).values(values[start : start + _CHUNK]))
    await _audit(
        ctx,
        "finance.import.create",
        finance_import.space_id,
        "finance_import",
        finance_import.id,
        rows=finance_import.row_count,
        encoding=encoding,
    )
    sample = [cells for _, cells in parsed.rows[:SAMPLE_ROWS]]
    suggested = csv_import.suggest_mapping(parsed.header, sample)
    if suggested is not None:
        await _apply_mapping(ctx, finance_import, suggested)
    return finance_import


def _check_preview(finance_import: FinanceImport) -> None:
    if finance_import.status != "preview":
        raise ProblemError(
            409,
            "import_not_in_preview",
            "The import was already validated or cancelled",
            extra={"import_status": finance_import.status},
        )
    if finance_import.expires_at < utcnow():
        raise ProblemError(409, "import_expired", "The preview expired: upload the file again")


async def set_mapping(ctx: Ctx, import_id: uuid.UUID, mapping: ImportMapping) -> FinanceImport:
    finance_import = await get_import(ctx, import_id, lock=True)
    await authorize(ctx, "finance.import", finance_import.space_id)
    _check_preview(finance_import)
    width = len(finance_import.header)
    columns = [
        mapping.date_column,
        mapping.label_column,
        mapping.amount_column,
        mapping.debit_column,
        mapping.credit_column,
        mapping.reference_column,
        mapping.currency_column,
    ]
    if any(c is not None and c >= width for c in columns):
        raise ProblemError(
            422, "mapping_invalid", "A column does not exist in this file", extra={"columns": width}
        )
    await _apply_mapping(ctx, finance_import, _mapping_of(mapping))
    await _audit(
        ctx,
        "finance.import.map",
        finance_import.space_id,
        "finance_import",
        finance_import.id,
        amount_mode=mapping.amount_mode,
        date_format=mapping.date_format,
    )
    return finance_import


async def _rules(
    ctx: Ctx, space_id: uuid.UUID
) -> dict[tuple[str, str], tuple[uuid.UUID, uuid.UUID]]:
    """(merchant key, direction) -> (rule id, category id), for the active categories."""
    rows = await ctx.db.execute(
        select(
            CategoryRule.merchant_key,
            FinanceCategory.kind,
            CategoryRule.id,
            CategoryRule.category_id,
        )
        .join(FinanceCategory, FinanceCategory.id == CategoryRule.category_id)
        .where(
            CategoryRule.user_id == ctx.principal.user_id,
            CategoryRule.space_id == space_id,
            FinanceCategory.archived.is_(False),
        )
    )
    return {(key, kind): (rule_id, category_id) for key, kind, rule_id, category_id in rows}


async def _apply_mapping(
    ctx: Ctx, finance_import: FinanceImport, mapping: csv_import.Mapping
) -> None:
    """Reads every row under the mapping and classifies it. Read-only towards transactions
    and rules (no hit is counted before the validation)."""
    rows = list(
        await ctx.db.scalars(
            select(FinanceImportRow)
            .where(FinanceImportRow.import_id == finance_import.id)
            .order_by(FinanceImportRow.line_no)
        )
    )
    # Updated in bulk below: the loaded copies would go stale.
    for row in rows:
        ctx.db.expunge(row)
    width = len(finance_import.header)
    today = local_today(ctx)
    rules = await _rules(ctx, finance_import.space_id)
    readings = [csv_import.read_row(r.line_no, r.cells, mapping, width) for r in rows]
    occurrences: Counter[tuple[Any, ...]] = Counter()
    keys: dict[int, str] = {}
    seen: set[str] = set()
    status: dict[int, str] = {}
    for reading in readings:
        if reading.reason is None and reading.tx_date is not None and reading.tx_date > today:
            reading.reason = "date_in_future"
        if reading.reason is not None:
            status[reading.line_no] = "invalid"
            continue
        assert reading.tx_date and reading.amount and reading.direction and reading.currency
        identity = (
            reading.tx_date,
            reading.direction,
            reading.amount,
            reading.currency,
            csv_import.fold(reading.label or ""),
        )
        occurrence = occurrences[identity]
        occurrences[identity] += 1
        key = csv_import.import_key(
            account=finance_import.account_label,
            tx_date=reading.tx_date,
            direction=reading.direction,
            amount=reading.amount,
            currency=reading.currency,
            label=reading.label,
            reference=reading.reference,
            occurrence=occurrence,
        )
        keys[reading.line_no] = key
        status[reading.line_no] = "in_file_duplicate" if key in seen else "valid"
        seen.add(key)

    # Already in the budget (an earlier import of the same operations).
    existing: set[str] = set()
    all_keys = list(set(keys.values()))
    for start in range(0, len(all_keys), _CHUNK):
        existing.update(
            k
            for k in await ctx.db.scalars(
                select(Transaction.import_key).where(
                    Transaction.user_id == ctx.principal.user_id,
                    Transaction.space_id == finance_import.space_id,
                    Transaction.import_key.in_(all_keys[start : start + _CHUNK]),
                )
            )
            if k is not None
        )
    for line_no, key in keys.items():
        if status[line_no] == "valid" and key in existing:
            status[line_no] = "already_imported"

    # Probably the same purchase as a ticket or a manual entry (not imported by default).
    valid = [r for r in readings if status.get(r.line_no) == "valid"]
    matches: dict[int, tuple[uuid.UUID, date]] = {}
    if valid:
        low = min(r.tx_date for r in valid if r.tx_date) - DUPLICATE_WINDOW
        high = max(r.tx_date for r in valid if r.tx_date) + DUPLICATE_WINDOW
        candidates = list(
            (
                await ctx.db.execute(
                    select(
                        Transaction.id,
                        Transaction.tx_date,
                        Transaction.amount,
                        Transaction.direction,
                        Transaction.currency,
                    ).where(
                        Transaction.user_id == ctx.principal.user_id,
                        Transaction.space_id == finance_import.space_id,
                        Transaction.source_type.in_(("manual", "receipt")),
                        Transaction.tx_date >= low,
                        Transaction.tx_date <= high,
                    )
                )
            ).all()
        )
        used: set[uuid.UUID] = set()
        for reading in valid:
            assert reading.tx_date is not None
            best: tuple[int, uuid.UUID, date] | None = None
            for tx_id, tx_date, amount, direction, currency in candidates:
                if (
                    tx_id in used
                    or direction != reading.direction
                    or currency != reading.currency
                    or _q(Decimal(amount)) != reading.amount
                ):
                    continue
                distance = abs((tx_date - reading.tx_date).days)
                if distance <= DUPLICATE_WINDOW.days and (best is None or distance < best[0]):
                    best = (distance, tx_id, tx_date)
            if best is not None:
                used.add(best[1])
                matches[reading.line_no] = (best[1], best[2])
                status[reading.line_no] = "possible_duplicate"

    updates = []
    for reading in readings:
        parsed: dict[str, Any] | None = None
        if reading.reason is None and reading.amount is not None:
            merchant = csv_import.clean_bank_label(reading.label or "")
            rule = rules.get((merchant_key(merchant) or "", reading.direction or ""))
            match = matches.get(reading.line_no)
            parsed = {
                "tx_date": reading.tx_date.isoformat() if reading.tx_date else None,
                "amount": str(_q(reading.amount)),
                "direction": reading.direction,
                "currency": reading.currency,
                "label": reading.label,
                "merchant": merchant,
                "reference": reading.reference,
                "category_id": str(rule[1]) if rule else None,
                "match_date": match[1].isoformat() if match else None,
            }
        updates.append(
            {
                "import_id": finance_import.id,
                "line_no": reading.line_no,
                "status": status[reading.line_no],
                "reason": reading.reason,
                "parsed": parsed,
                "import_key": keys.get(reading.line_no),
                "match_id": matches[reading.line_no][0] if reading.line_no in matches else None,
            }
        )
    if updates:
        await ctx.db.execute(update(FinanceImportRow), updates)
    finance_import.mapping = ImportMapping.model_validate(mapping.__dict__).model_dump()
    await ctx.db.flush()


# --- Validation, undo, cancellation ----------------------------------------------------------


async def commit_import(ctx: Ctx, import_id: uuid.UUID, choice: ImportCommit) -> FinanceImport:
    """The owner's validation: the chosen rows become transactions (source « import »),
    categorised by the learned rules (correctable), never twice (unique import key)."""
    finance_import = await get_import(ctx, import_id, lock=True)
    await authorize(ctx, "finance.import", finance_import.space_id)
    _check_preview(finance_import)
    if finance_import.mapping is None:
        raise ProblemError(409, "import_not_mapped", "Choose the columns before importing")
    excluded, included = set(choice.exclude_lines), set(choice.include_lines)
    rows = [
        row
        for row in await ctx.db.scalars(
            select(FinanceImportRow)
            .where(
                FinanceImportRow.import_id == finance_import.id,
                FinanceImportRow.status.in_(("valid", "possible_duplicate")),
            )
            .order_by(FinanceImportRow.line_no)
        )
        if (row.status == "valid" and row.line_no not in excluded)
        or (row.status == "possible_duplicate" and row.line_no in included)
    ]
    rules = await _rules(ctx, finance_import.space_id)
    rule_of_key: dict[str, uuid.UUID] = {}
    values = []
    for row in rows:
        parsed = row.parsed or {}
        if row.import_key is None or not parsed.get("amount"):
            continue
        merchant = parsed.get("merchant")
        rule = rules.get((merchant_key(merchant) or "", parsed["direction"]))
        if rule is not None:
            rule_of_key[row.import_key] = rule[0]
        values.append(
            {
                "id": uuid.uuid4(),
                "user_id": finance_import.user_id,
                "space_id": finance_import.space_id,
                "tx_date": date.fromisoformat(parsed["tx_date"]),
                "amount": _q(Decimal(parsed["amount"])),
                "direction": parsed["direction"],
                "currency": parsed["currency"],
                "merchant": merchant,
                "label": parsed.get("label"),
                "category_id": rule[1] if rule else None,
                "category_confirmed": False,
                "source_type": "import",
                "import_id": finance_import.id,
                "import_key": row.import_key,
            }
        )
    inserted: list[str] = []
    for start in range(0, len(values), _CHUNK):
        result = await ctx.db.execute(
            insert(Transaction)
            .values(values[start : start + _CHUNK])
            .on_conflict_do_nothing(
                index_elements=["space_id", "import_key"],
                index_where=Transaction.import_key.is_not(None),
            )
            .returning(Transaction.import_key)
        )
        inserted.extend(k for k in result.scalars() if k is not None)
    hits = Counter(rule_of_key[k] for k in inserted if k in rule_of_key)
    for rule_id, count in hits.items():
        await ctx.db.execute(
            update(CategoryRule)
            .where(CategoryRule.id == rule_id)
            .values(hits=CategoryRule.hits + count)
        )
    # The preview's cells (bank labels) are not kept once validated.
    await ctx.db.execute(
        delete(FinanceImportRow).where(FinanceImportRow.import_id == finance_import.id)
    )
    finance_import.status = "committed"
    finance_import.committed_at = utcnow()
    finance_import.imported_count = len(inserted)
    finance_import.skipped_count = finance_import.row_count - len(inserted)
    await ctx.db.flush()
    await _audit(
        ctx,
        "finance.import.commit",
        finance_import.space_id,
        "finance_import",
        finance_import.id,
        imported=len(inserted),
        skipped=finance_import.skipped_count,
        auto_categorised=sum(hits.values()),
    )
    return finance_import


async def undo_import(ctx: Ctx, import_id: uuid.UUID) -> FinanceImport:
    """Deletes the operations of a validated import that are still there; the owner's
    corrections on them are lost (their number is reported)."""
    finance_import = await get_import(ctx, import_id, lock=True)
    await authorize(ctx, "finance.import.undo", finance_import.space_id)
    if finance_import.status != "committed":
        raise ProblemError(
            409,
            "import_not_committed",
            "Only a validated import can be undone",
            extra={"import_status": finance_import.status},
        )
    deleted, edited = await erasure.undo_import(ctx.db, finance_import)
    await _audit(
        ctx,
        "finance.import.undo",
        finance_import.space_id,
        "finance_import",
        finance_import.id,
        deleted=deleted,
        edited=edited,
    )
    return finance_import


async def discard_import(ctx: Ctx, import_id: uuid.UUID) -> None:
    """Cancels a preview: its rows go with it, nothing was ever written to the budget."""
    finance_import = await get_import(ctx, import_id, lock=True)
    await authorize(ctx, "finance.import", finance_import.space_id)
    if finance_import.status != "preview":
        raise ProblemError(
            409,
            "import_not_in_preview",
            "The import was already validated or cancelled",
            extra={"import_status": finance_import.status},
        )
    space_id, target = finance_import.space_id, finance_import.id
    await ctx.db.delete(finance_import)
    await ctx.db.flush()
    await _audit(ctx, "finance.import.discard", space_id, "finance_import", target)


async def purge_expired_imports(session: Any) -> int:
    """Previews left unvalidated past their expiry (their rows hold bank labels)."""
    result = await session.execute(
        delete(FinanceImport).where(
            FinanceImport.status == "preview", FinanceImport.expires_at < utcnow()
        )
    )
    return int(result.rowcount or 0)
