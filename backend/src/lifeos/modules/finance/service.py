"""Basic finances (F04): categories and budgets, transactions, receipts (UC02).

Money is exact (Decimal, numeric(14, 2)) end to end, and every figure is computed locally
in SQL: the budget works without any cloud access. « Classer une dépense » is automatic
(learned merchant rules, logged) and always correctable by the owner. Nothing is ever
recorded from a receipt without the owner posting the transaction.
"""

import base64
import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import MAX_LIMIT, Page
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.models import Document
from lifeos.modules.finance.models import CategoryRule, FinanceCategory, Transaction
from lifeos.modules.finance.schemas import (
    BudgetAlert,
    BudgetSnapshot,
    BudgetSummary,
    CategoryCreate,
    CategorySummary,
    CategoryUpdate,
    ReceiptLines,
    ReceiptProposalOut,
    SuggestedCategory,
    SummaryTotals,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
    UncategorisedSummary,
    is_month,
)
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")
ALERT_RATIO = 0.8

DEFAULT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Alimentation", "expense"),
    ("Logement", "expense"),
    ("Transports", "expense"),
    ("Santé", "expense"),
    ("Loisirs", "expense"),
    ("Restaurants", "expense"),
    ("Habillement", "expense"),
    ("Abonnements", "expense"),
    ("Divers", "expense"),
    ("Salaire", "income"),
    ("Autres revenus", "income"),
)

# Merchant words -> default category name: a suggestion for receipts only, never applied
# without the owner (learned rules take precedence). Unambiguous names only.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Alimentation": (
        "carrefour",
        "leclerc",
        "auchan",
        "intermarche",
        "lidl",
        "aldi",
        "monoprix",
        "franprix",
        "super u",
        "hyper u",
        "picard",
        "biocoop",
        "boulangerie",
        "boucherie",
        "fromagerie",
        "primeur",
        "epicerie",
        "supermarche",
    ),
    "Restaurants": (
        "restaurant",
        "bistrot",
        "brasserie",
        "pizzeria",
        "creperie",
        "sushi",
        "mcdonald",
        "burger king",
    ),
    "Transports": ("sncf", "ratp", "station service", "parking", "peage", "taxi"),
    "Santé": ("pharmacie", "parapharmacie", "opticien", "dentiste"),
    "Habillement": ("kiabi", "zara", "uniqlo", "celio", "jules", "chaussures"),
    "Loisirs": ("cinema", "fnac", "cultura", "librairie", "musee", "theatre"),
}


# --- Helpers ---------------------------------------------------------------------------------


def _q(value: Decimal) -> Decimal:
    """Same representation before and after a round trip through numeric(14, 2)."""
    return value.quantize(_CENTS)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def merchant_key(merchant: str | None) -> str | None:
    """Normalised merchant: lowercase, no accents, no digits nor punctuation.

    « CARREFOUR MARKET 1234 », « Carrefour Market » and « carrefour-market » share a key.
    """
    if not merchant:
        return None
    key = " ".join(re.findall(r"[a-z]+", _fold(merchant)))[:80]
    return key or None


def local_today(ctx: Ctx, now: datetime | None = None) -> date:
    """Today in the owner's time zone (a transaction date is a local calendar date)."""
    return (now or utcnow()).astimezone(ZoneInfo(ctx.user.timezone)).date()


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a YYYY-MM month."""
    if not is_month(month) or int(month[:4]) < 1900:
        raise ProblemError(422, "invalid_month", "The month must be formatted as YYYY-MM")
    year, number = int(month[:4]), int(month[5:7])
    start = date(year, number, 1)
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    return start, following - timedelta(days=1)


def _readable(ctx: Ctx, space_id: uuid.UUID | None) -> frozenset[uuid.UUID]:
    """Spaces whose finances the principal may read (an agent needs an explicit grant)."""
    spaces = ctx.principal.spaces_or_all(space_id)
    allowed = frozenset(s for s in spaces if ctx.principal.can(s, "finances", "read"))
    if space_id is not None and not allowed:
        raise ProblemError(
            403, "action_denied", "Action not allowed", extra={"reason": "no_permission"}
        )
    return allowed


async def _audit(
    ctx: Ctx,
    action: str,
    space_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    **details: Any,
) -> None:
    # Never amounts nor merchants in the audit trail: only what happened.
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )


# --- Categories ------------------------------------------------------------------------------


def _category_exists() -> ProblemError:
    return ProblemError(409, "category_exists", "A category with this name already exists")


async def list_categories(
    ctx: Ctx, *, space_id: uuid.UUID | None, include_archived: bool = False
) -> list[FinanceCategory]:
    spaces = _readable(ctx, space_id)
    stmt = select(FinanceCategory).where(
        FinanceCategory.user_id == ctx.principal.user_id, FinanceCategory.space_id.in_(spaces)
    )
    if not include_archived:
        stmt = stmt.where(FinanceCategory.archived.is_(False))
    rows = list(await ctx.db.scalars(stmt.limit(500)))
    return sorted(rows, key=lambda c: (c.kind != "expense", _fold(c.name), c.created_at))


async def get_category(ctx: Ctx, category_id: uuid.UUID) -> FinanceCategory:
    category = await ctx.db.scalar(
        select(FinanceCategory).where(
            FinanceCategory.id == category_id,
            FinanceCategory.user_id == ctx.principal.user_id,
            FinanceCategory.space_id.in_(_readable(ctx, None)),
        )
    )
    if category is None:
        raise not_found("category_not_found", "Category not found")
    return category


async def _ensure_unique_name(
    ctx: Ctx, space_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
) -> None:
    stmt = select(FinanceCategory.id).where(
        FinanceCategory.user_id == ctx.principal.user_id,
        FinanceCategory.space_id == space_id,
        func.lower(FinanceCategory.name) == name.lower(),
    )
    if exclude is not None:
        stmt = stmt.where(FinanceCategory.id != exclude)
    if await ctx.db.scalar(stmt.limit(1)) is not None:
        raise _category_exists()


async def _flush_unique(ctx: Ctx) -> None:
    try:
        async with ctx.db.begin_nested():
            await ctx.db.flush()
    except IntegrityError as exc:  # concurrent creation of the same name
        raise _category_exists() from exc


async def create_category(ctx: Ctx, data: CategoryCreate) -> FinanceCategory:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "finance.write", data.space_id)
    await _ensure_unique_name(ctx, data.space_id, data.name)
    category = FinanceCategory(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        name=data.name,
        kind=data.kind,
        monthly_budget=_q(data.monthly_budget) if data.monthly_budget is not None else None,
    )
    ctx.db.add(category)
    await _flush_unique(ctx)
    await _audit(ctx, "finance.category.create", category.space_id, "finance_category", category.id)
    return category


async def update_category(
    ctx: Ctx, category_id: uuid.UUID, data: CategoryUpdate
) -> FinanceCategory:
    category = await get_category(ctx, category_id)
    await authorize(ctx, "finance.write", category.space_id)
    changes = data.model_dump(exclude_unset=True)
    for key in ("name", "kind", "archived"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    if "name" in changes and changes["name"] != category.name:
        await _ensure_unique_name(ctx, category.space_id, changes["name"], exclude=category.id)
    if "kind" in changes and changes["kind"] != category.kind:
        used = await ctx.db.scalar(
            select(Transaction.id).where(Transaction.category_id == category.id).limit(1)
        )
        if used is not None:
            raise ProblemError(
                409,
                "category_in_use",
                "The category has transactions",
                "Its kind (expense or income) cannot change while transactions use it.",
            )
    for key, value in changes.items():
        if key == "monthly_budget":
            value = _q(value) if value is not None else None
        setattr(category, key, value)
    await _flush_unique(ctx)
    await _audit(
        ctx,
        "finance.category.update",
        category.space_id,
        "finance_category",
        category.id,
        fields=",".join(sorted(changes)),
    )
    return category


async def delete_category(ctx: Ctx, category_id: uuid.UUID) -> None:
    """Its transactions become uncategorised; its learned rules go with it."""
    category = await get_category(ctx, category_id)
    await authorize(ctx, "finance.write", category.space_id)
    await ctx.db.delete(category)
    await ctx.db.flush()
    await _audit(ctx, "finance.category.delete", category.space_id, "finance_category", category.id)


async def create_defaults(ctx: Ctx, space_id: uuid.UUID) -> list[FinanceCategory]:
    """Default French categories for a space; idempotent (existing names are kept)."""
    ctx.principal.require_space(space_id)
    await authorize(ctx, "finance.write", space_id)
    existing = {
        name.lower()
        for name in await ctx.db.scalars(
            select(FinanceCategory.name).where(
                FinanceCategory.user_id == ctx.principal.user_id,
                FinanceCategory.space_id == space_id,
            )
        )
    }
    missing = [(n, k) for n, k in DEFAULT_CATEGORIES if n.lower() not in existing]
    if missing:
        result = await ctx.db.execute(
            insert(FinanceCategory)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "user_id": ctx.principal.user_id,
                        "space_id": space_id,
                        "name": name,
                        "kind": kind,
                    }
                    for name, kind in missing
                ]
            )
            .on_conflict_do_nothing(constraint="uq_finance_categories_name")
            .returning(FinanceCategory.id)
        )
        created = len(result.all())
        await audit.record(
            ctx.db,
            action="finance.category.defaults",
            actor_type=ctx.principal.kind,
            actor_id=ctx.principal.actor_id,
            user_id=ctx.principal.user_id,
            space_id=space_id,
            details={"created": created},
        )
    return await list_categories(ctx, space_id=space_id)


# --- Rules (automatic categorisation) --------------------------------------------------------


async def _rule_category(
    ctx: Ctx, space_id: uuid.UUID, merchant: str | None, direction: str
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(rule id, category id) learned for this merchant, if its category still fits."""
    key = merchant_key(merchant)
    if key is None:
        return None
    row = (
        await ctx.db.execute(
            select(CategoryRule.id, CategoryRule.category_id)
            .join(FinanceCategory, FinanceCategory.id == CategoryRule.category_id)
            .where(
                CategoryRule.user_id == ctx.principal.user_id,
                CategoryRule.space_id == space_id,
                CategoryRule.merchant_key == key,
                FinanceCategory.archived.is_(False),
                FinanceCategory.kind == direction,
            )
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


async def _apply_rule(
    ctx: Ctx, space_id: uuid.UUID, merchant: str | None, direction: str
) -> uuid.UUID | None:
    found = await _rule_category(ctx, space_id, merchant, direction)
    if found is None:
        return None
    rule_id, category_id = found
    await ctx.db.execute(
        update(CategoryRule).where(CategoryRule.id == rule_id).values(hits=CategoryRule.hits + 1)
    )
    return category_id


async def _learn(
    ctx: Ctx, space_id: uuid.UUID, merchant: str | None, category_id: uuid.UUID
) -> None:
    """The owner chose this category for this merchant: next time, it is automatic."""
    key = merchant_key(merchant)
    if key is None:
        return
    stmt = insert(CategoryRule).values(
        id=uuid.uuid4(),
        user_id=ctx.principal.user_id,
        space_id=space_id,
        merchant_key=key,
        category_id=category_id,
        hits=0,
    )
    await ctx.db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_finance_category_rules_merchant",
            set_={
                "category_id": stmt.excluded.category_id,
                # Hits count automatic uses of the current choice.
                "hits": case(
                    (CategoryRule.category_id == stmt.excluded.category_id, CategoryRule.hits),
                    else_=0,
                ),
                "updated_at": utcnow(),
            },
        )
    )


# --- Transactions ----------------------------------------------------------------------------


async def _category_for(
    ctx: Ctx, category_id: uuid.UUID, space_id: uuid.UUID, direction: str
) -> FinanceCategory:
    category = await get_category(ctx, category_id)
    if category.space_id != space_id:
        raise ProblemError(422, "category_space_mismatch", "The category belongs to another space")
    if category.archived:
        raise ProblemError(422, "category_archived", "The category is archived")
    if category.kind != direction:
        raise ProblemError(
            422,
            "category_kind_mismatch",
            "The category does not match the transaction direction",
            extra={"category_kind": category.kind},
        )
    return category


async def _receipt_document(ctx: Ctx, document_id: uuid.UUID, space_id: uuid.UUID) -> Document:
    document = await ctx.db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == ctx.principal.user_id,
            Document.space_id.in_(ctx.principal.space_ids),
        )
    )
    if document is None or not ctx.principal.can(document.space_id, "documents", "read"):
        raise not_found("document_not_found", "Document not found")
    if document.space_id != space_id:
        raise ProblemError(422, "document_space_mismatch", "The document belongs to another space")
    if document.purpose != "receipt":
        raise ProblemError(422, "not_a_receipt", "The document is not a receipt")
    return document


def _check_date(ctx: Ctx, tx_date: date) -> None:
    if tx_date > local_today(ctx):
        raise ProblemError(422, "tx_date_in_future", "The transaction date is in the future")


async def get_transaction(ctx: Ctx, transaction_id: uuid.UUID) -> Transaction:
    transaction = await ctx.db.scalar(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == ctx.principal.user_id,
            Transaction.space_id.in_(_readable(ctx, None)),
        )
    )
    if transaction is None:
        raise not_found("transaction_not_found", "Transaction not found")
    return transaction


def _encode_cursor(transaction: Transaction) -> str:
    raw = json.dumps(
        [
            transaction.tx_date.isoformat(),
            transaction.created_at.isoformat(),
            str(transaction.id),
        ]
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[date, datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        tx_date, created, row_id = json.loads(raw)
        return date.fromisoformat(tx_date), datetime.fromisoformat(created), uuid.UUID(row_id)
    except (ValueError, TypeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor") from exc


async def list_transactions(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    month: str | None = None,
    category_id: uuid.UUID | None = None,
    uncategorised: bool = False,
    direction: str | None = None,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[TransactionOut]:
    """Most recent first (transaction date, then entry); keyset pagination."""
    spaces = _readable(ctx, space_id)
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(Transaction).where(
        Transaction.user_id == ctx.principal.user_id, Transaction.space_id.in_(spaces)
    )
    if month:
        start, end = month_bounds(month)
        stmt = stmt.where(Transaction.tx_date >= start, Transaction.tx_date <= end)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if uncategorised:
        stmt = stmt.where(Transaction.category_id.is_(None))
    if direction:
        stmt = stmt.where(Transaction.direction == direction)
    if q and q.strip():
        stmt = stmt.where(
            Transaction.search_vector.op("@@")(func.websearch_to_tsquery("lifeos_fr", q))
        )
    if cursor:
        tx_date, created, row_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Transaction.tx_date, Transaction.created_at, Transaction.id)
            < tuple_(tx_date, created, row_id)
        )
    rows = list(
        await ctx.db.scalars(
            stmt.order_by(
                Transaction.tx_date.desc(), Transaction.created_at.desc(), Transaction.id.desc()
            ).limit(limit + 1)
        )
    )
    items = rows[:limit]
    return Page[TransactionOut](
        items=[TransactionOut.model_validate(t) for t in items],
        next_cursor=_encode_cursor(items[-1]) if len(rows) > limit else None,
    )


async def create_transaction(ctx: Ctx, data: TransactionCreate) -> Transaction:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "finance.write", data.space_id)
    _check_date(ctx, data.tx_date)
    if data.source_type == "receipt" and data.document_id is None:
        raise ProblemError(
            422, "receipt_document_required", "A transaction from a receipt needs its document"
        )
    if data.document_id is not None:
        await _receipt_document(ctx, data.document_id, data.space_id)
    if data.category_id is not None:
        await _category_for(ctx, data.category_id, data.space_id, data.direction)
        category_id: uuid.UUID | None = data.category_id
    else:
        # « Classer une dépense »: automatic from the learned rule, correctable later.
        category_id = await _apply_rule(ctx, data.space_id, data.merchant, data.direction)
    transaction = Transaction(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        tx_date=data.tx_date,
        amount=_q(data.amount),
        direction=data.direction,
        currency=data.currency,
        merchant=data.merchant,
        label=data.label,
        notes=data.notes,
        category_id=category_id,
        category_confirmed=data.category_id is not None,
        source_type=data.source_type,
        document_id=data.document_id,
    )
    ctx.db.add(transaction)
    await ctx.db.flush()
    if data.category_id is not None:
        await _learn(ctx, data.space_id, data.merchant, data.category_id)
    await _audit(
        ctx,
        "finance.transaction.create",
        transaction.space_id,
        "transaction",
        transaction.id,
        source_type=transaction.source_type,
        auto_categorised=data.category_id is None and category_id is not None,
    )
    return transaction


async def update_transaction(
    ctx: Ctx, transaction_id: uuid.UUID, data: TransactionUpdate, *, if_match: int | None = None
) -> Transaction:
    transaction = await get_transaction(ctx, transaction_id)
    await authorize(ctx, "finance.write", transaction.space_id)
    if if_match is not None and if_match != transaction.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The transaction was modified elsewhere",
            extra={"current_version": transaction.version},
        )
    changes = data.model_dump(exclude_unset=True)
    for key in ("amount", "direction", "currency", "tx_date"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    if "tx_date" in changes:
        _check_date(ctx, changes["tx_date"])
    direction = changes.get("direction", transaction.direction)
    merchant = changes.get("merchant", transaction.merchant)
    category_id, confirmed = transaction.category_id, transaction.category_confirmed
    # Everything is checked before the first change: one flush, one version increment.
    if "category_id" in changes:
        category_id = changes["category_id"]
        if category_id is not None:
            await _category_for(ctx, category_id, transaction.space_id, direction)
        # Chosen, confirmed or removed by the owner.
        confirmed = category_id is not None
    else:
        if category_id is not None and direction != transaction.direction:
            current = await ctx.db.get(FinanceCategory, category_id)
            if current is not None and current.kind != direction:
                if confirmed:
                    raise ProblemError(
                        422,
                        "category_kind_mismatch",
                        "The category does not match the transaction direction",
                        extra={"category_kind": current.kind},
                    )
                category_id = None  # an automatic choice that no longer fits
        if not confirmed and "merchant" in changes:
            learned = await _apply_rule(ctx, transaction.space_id, merchant, direction)
            category_id = learned or category_id
    for key in ("amount", "direction", "currency", "tx_date", "merchant", "label", "notes"):
        if key in changes:
            value = changes[key]
            setattr(transaction, key, _q(value) if key == "amount" else value)
    transaction.category_id = category_id
    transaction.category_confirmed = confirmed
    await ctx.db.flush()
    if "category_id" in changes and category_id is not None:
        await _learn(ctx, transaction.space_id, merchant, category_id)
    await _audit(
        ctx,
        "finance.transaction.update",
        transaction.space_id,
        "transaction",
        transaction.id,
        fields=",".join(sorted(changes)),
    )
    return transaction


async def delete_transaction(ctx: Ctx, transaction_id: uuid.UUID) -> None:
    transaction = await get_transaction(ctx, transaction_id)
    await authorize(ctx, "finance.write", transaction.space_id)
    await ctx.db.delete(transaction)
    await ctx.db.flush()
    await _audit(
        ctx, "finance.transaction.delete", transaction.space_id, "transaction", transaction.id
    )


# --- Receipts --------------------------------------------------------------------------------


def as_amount(value: object) -> Decimal | None:
    """A positive amount read from a stored analysis (strings), or None."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return _q(amount) if amount.is_finite() and amount > 0 else None


def as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _strings(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


async def suggest_category(
    ctx: Ctx, space_id: uuid.UUID, merchant: str | None, direction: str = "expense"
) -> SuggestedCategory | None:
    """Learned rule first, then an unambiguous merchant keyword (no side effect)."""
    found = await _rule_category(ctx, space_id, merchant, direction)
    if found is not None:
        category = await ctx.db.get(FinanceCategory, found[1])
        if category is not None:
            return SuggestedCategory(id=category.id, name=category.name, source="rule")
    key = merchant_key(merchant)
    if key is None or direction != "expense":
        return None
    for name, words in _KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(word)}\b", key) for word in words):
            category = await ctx.db.scalar(
                select(FinanceCategory).where(
                    FinanceCategory.user_id == ctx.principal.user_id,
                    FinanceCategory.space_id == space_id,
                    FinanceCategory.kind == "expense",
                    FinanceCategory.archived.is_(False),
                    func.lower(FinanceCategory.name) == name.lower(),
                )
            )
            if category is not None:
                return SuggestedCategory(id=category.id, name=category.name, source="keyword")
    return None


async def receipt_proposal(ctx: Ctx, document_id: uuid.UUID) -> ReceiptProposalOut:
    """The values read locally from a receipt, for the owner to check and correct."""
    document = await ctx.db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == ctx.principal.user_id,
            Document.space_id.in_(_readable(ctx, None)),
        )
    )
    if document is None or not ctx.principal.can(document.space_id, "documents", "read"):
        raise not_found("document_not_found", "Document not found")
    if document.purpose != "receipt":
        raise ProblemError(422, "not_a_receipt", "The document is not a receipt")
    receipt = (document.analysis or {}).get("receipt")
    if document.status != "ready" or not isinstance(receipt, dict):
        raise ProblemError(
            409,
            "receipt_not_ready",
            "The receipt has not been processed yet",
            extra={"status": document.status},
        )
    # Stored by the document pipeline (ReceiptProposal.as_dict()); read defensively.
    raw_lines = receipt.get("lines")
    lines = ReceiptLines(
        **{
            key: value
            for key in ("merchant", "tx_date", "total")
            if isinstance(raw_lines, dict) and isinstance(value := raw_lines.get(key), str)
        }
    )
    merchant = receipt.get("merchant") if isinstance(receipt.get("merchant"), str) else None
    currency = receipt.get("currency")
    if not (isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency)):
        currency = "EUR"
    try:
        confidence = float(receipt.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    recorded = await ctx.db.scalars(
        select(Transaction.id)
        .where(
            Transaction.user_id == ctx.principal.user_id,
            Transaction.document_id == document.id,
        )
        .order_by(Transaction.created_at)
    )
    return ReceiptProposalOut(
        document_id=document.id,
        space_id=document.space_id,
        title=document.title,
        merchant=merchant,
        tx_date=as_date(receipt.get("tx_date")),
        total=as_amount(receipt.get("total")),
        currency=currency,
        vat_rates=_strings(receipt.get("vat_rates")),
        card_payment=bool(receipt.get("card_payment")),
        confidence=max(0.0, min(1.0, confidence)),
        warnings=_strings(receipt.get("warnings")),
        lines=lines,
        suggested_category=await suggest_category(ctx, document.space_id, merchant),
        recorded_transaction_ids=list(recorded),
    )


# --- Budget summary --------------------------------------------------------------------------


def _ratio(spent: Decimal, budget: Decimal | None) -> float | None:
    if budget is None or budget <= 0:
        return None
    return round(float(spent / budget), 4)


async def summary(
    ctx: Ctx, *, space_id: uuid.UUID, month: str | None = None, currency: str = "EUR"
) -> BudgetSummary:
    """Month budget per category, in exact decimals, entirely in local SQL."""
    _readable(ctx, space_id)
    month = month or f"{local_today(ctx):%Y-%m}"
    start, end = month_bounds(month)
    uid = ctx.principal.user_id
    categories = list(
        await ctx.db.scalars(
            select(FinanceCategory).where(
                FinanceCategory.user_id == uid, FinanceCategory.space_id == space_id
            )
        )
    )
    in_month = (
        Transaction.user_id == uid,
        Transaction.space_id == space_id,
        Transaction.tx_date >= start,
        Transaction.tx_date <= end,
    )
    sums: dict[tuple[uuid.UUID | None, str], tuple[Decimal, int]] = {}
    for category_id, direction, total, count in await ctx.db.execute(
        select(
            Transaction.category_id,
            Transaction.direction,
            func.coalesce(func.sum(Transaction.amount), 0),
            func.count(),
        )
        .where(*in_month, Transaction.currency == currency)
        .group_by(Transaction.category_id, Transaction.direction)
    ):
        sums[(category_id, direction)] = (_q(Decimal(total)), int(count))
    other = await ctx.db.scalar(
        select(func.count()).where(*in_month, Transaction.currency != currency)
    )

    def take(category_id: uuid.UUID | None, direction: str) -> tuple[Decimal, int]:
        return sums.pop((category_id, direction), (_ZERO, 0))

    rows: list[CategorySummary] = []
    budget_total = budgeted_spent = _ZERO
    for category in sorted(categories, key=lambda c: (c.kind != "expense", _fold(c.name))):
        spent, spent_count = take(category.id, "expense")
        received, received_count = take(category.id, "income")
        count = spent_count + received_count
        if category.archived and count == 0:
            continue
        budget = category.monthly_budget
        if budget is not None and not category.archived and category.kind == "expense":
            budget_total += budget
            budgeted_spent += spent
        rows.append(
            CategorySummary(
                category_id=category.id,
                name=category.name,
                kind=category.kind,
                archived=category.archived,
                budget=budget,
                spent=spent,
                received=received,
                remaining=budget - spent if budget is not None else None,
                ratio=_ratio(spent, budget),
                over_budget=budget is not None and spent > budget,
                transactions=count,
            )
        )
    # Whatever is left has no (visible) category.
    uncategorised_expenses = uncategorised_income = _ZERO
    uncategorised_count = 0
    for (_, direction), (total, count) in sums.items():
        if direction == "expense":
            uncategorised_expenses += total
        else:
            uncategorised_income += total
        uncategorised_count += count
    expenses = sum((r.spent for r in rows), uncategorised_expenses)
    income = sum((r.received for r in rows), uncategorised_income)
    return BudgetSummary(
        space_id=space_id,
        month=month,
        currency=currency,
        period_start=start,
        period_end=end,
        categories=rows,
        uncategorised=UncategorisedSummary(
            expenses=uncategorised_expenses,
            income=uncategorised_income,
            transactions=uncategorised_count,
        ),
        totals=SummaryTotals(
            expenses=expenses,
            income=income,
            balance=income - expenses,
            budget=budget_total,
            budgeted_spent=budgeted_spent,
            budget_remaining=budget_total - budgeted_spent,
        ),
        other_currency_transactions=int(other or 0),
    )


async def budget_snapshot(ctx: Ctx, space_id: uuid.UUID) -> BudgetSnapshot | None:
    """Current month for the dashboard; None while the space has no finance data."""
    if not ctx.principal.can(space_id, "finances", "read"):
        return None
    result = await summary(ctx, space_id=space_id)
    if not result.categories and result.uncategorised.transactions == 0:
        return None
    alerts = sorted(
        (
            c
            for c in result.categories
            if c.kind == "expense"
            and not c.archived
            and c.budget is not None
            and c.ratio is not None
            and c.ratio >= ALERT_RATIO
        ),
        key=lambda c: c.ratio or 0.0,
        reverse=True,
    )[:3]
    return BudgetSnapshot(
        space_id=space_id,
        month=result.month,
        currency=result.currency,
        spent=result.totals.expenses,
        budget=result.totals.budget,
        alerts=[
            BudgetAlert(
                category_id=c.category_id,
                name=c.name,
                budget=c.budget or _ZERO,
                spent=c.spent,
                ratio=c.ratio or 0.0,
            )
            for c in alerts
        ],
    )
