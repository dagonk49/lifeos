"""Erasure (docs/politique-de-donnees.md §2): what goes with a deleted object, and the
replay of erasures after a restore.

Deleting a row removes its links (foreign keys ON DELETE CASCADE) and its vectors (database
triggers `<table>_delete_embeddings`). What no key points to is removed here, for the
owner's deletions and for the replay alike:

- the stored reply of an idempotent creation (`idempotency_keys`): replaced by a 410
  problem, so that a retried request neither returns the content nor creates it again;
- the approvals that quote a deleted journal entry or conversation (proposed tasks, facts);
- the notifications about a deleted task or review (« Rappel : <titre> »);
- in past reviews, the titles and statements of deleted tasks, goals and facts, and the
  assistant's reading built from them or from a deleted journal entry.

Derived objects (facts and tasks proposed from a journal entry or a conversation, memories
saved from a message) stay unless the owner asks for them too (`derived_from`).
"""

import csv
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.idempotency import IdempotencyKey
from lifeos.modules.audit import service as audit
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.finance.models import FinanceCategory, Transaction
from lifeos.modules.goals.models import Goal, GoalMilestone
from lifeos.modules.guardian.models import Approval
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.notifications.models import Notification
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks.models import Task

Erasable = (
    Fact
    | Memory
    | JournalEntry
    | Task
    | Conversation
    | Message
    | Document
    | Goal
    | Review
    | Transaction
    | FinanceCategory
)

# What a retried idempotent creation gets once its object was erased.
ERASED_STATUS = 410
ERASED_BODY: dict[str, Any] = {
    "type": "urn:lifeos:problem:erased",
    "title": "This object was erased",
    "status": ERASED_STATUS,
    "code": "erased",
    "detail": "It was deleted after this request and is not created again.",
}
# `narrative_error` of a review whose reading was withdrawn (shown by the web client).
NARRATIVE_ERASED = "source_erased"
# A correction history longer than this is not a real one.
MAX_VERSIONS = 1000

# Any value, at any depth, equal to one of `$v` (lax mode: other types never match).
_ANY_OF: ColumnElement[Any] = literal_column("'$.** ? (@ == $v[*])'::jsonpath")


def _any_of(column: Any, values: Sequence[str]) -> ColumnElement[bool]:
    matched: ColumnElement[bool] = func.jsonb_path_exists(
        column, _ANY_OF, literal({"v": list(values)}, JSONB)
    )
    return matched


async def scrub_idempotency(
    session: AsyncSession, user_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> int:
    """Stored replies that hold one of `ids` become a 410 problem. The key itself stays
    until it expires: a retry of the same request must not create the object again."""
    values = sorted({str(i) for i in ids})
    if not values:
        return 0
    result = await session.execute(
        update(IdempotencyKey)
        .where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.response_status.is_not(None),
            IdempotencyKey.response_status != ERASED_STATUS,
            _any_of(IdempotencyKey.response_body, values),
        )
        .values(response_status=ERASED_STATUS, response_body=ERASED_BODY)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# Approvals whose parameters name a source object: (action, parameter).
APPROVAL_SOURCES = {
    "journal_entry": ("journal.apply_proposals", "entry_id"),
    "conversation": ("chat.apply_proposals", "conversation_id"),
}


async def forget_approvals(
    session: AsyncSession, user_id: uuid.UUID, source_type: str, ids: Iterable[uuid.UUID]
) -> int:
    """Approvals (pending or decided) that quote the deleted source: their parameters and
    preview hold the proposed texts."""
    action, key = APPROVAL_SOURCES[source_type]
    values = [str(i) for i in ids]
    if not values:
        return 0
    result = await session.execute(
        delete(Approval).where(
            Approval.user_id == user_id,
            Approval.action == action,
            Approval.params.op("->>")(key).in_(values),
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def forget_notifications(
    session: AsyncSession, user_id: uuid.UUID, source_type: str, ids: Iterable[uuid.UUID]
) -> int:
    values = list(ids)
    if not values:
        return 0
    result = await session.execute(
        delete(Notification).where(
            Notification.user_id == user_id,
            Notification.source_type == source_type,
            Notification.source_id.in_(values),
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def _mentions(value: Any, texts: set[str]) -> bool:
    if isinstance(value, str):
        return value in texts
    if isinstance(value, dict):
        return any(isinstance(v, str) and v in texts for v in value.values())
    return False


def _strip(value: Any, texts: set[str]) -> Any:
    """A copy without the list items that are, or are named by, one of `texts`."""
    if isinstance(value, list):
        return [_strip(item, texts) for item in value if not _mentions(item, texts)]
    if isinstance(value, dict):
        return {key: _strip(item, texts) for key, item in value.items()}
    return value


async def scrub_reviews(
    session: AsyncSession,
    user_id: uuid.UUID,
    space_id: uuid.UUID,
    *,
    texts: Sequence[str] = (),
    day: date | None = None,
) -> int:
    """Past reviews of the space: the deleted titles and statements leave their facts, and
    the assistant's reading (written from those facts and from the journal of the period)
    is withdrawn. Counters are not recomputed: « Établir à nouveau » does it."""
    wanted = sorted({t for t in texts if t})
    conditions: list[ColumnElement[bool]] = []
    if wanted:
        conditions.append(_any_of(Review.facts, wanted))
    if day is not None:
        conditions.append(and_(Review.period_start <= day, Review.period_end >= day))
    if not conditions:
        return 0
    reviews = await session.scalars(
        select(Review)
        .where(Review.user_id == user_id, Review.space_id == space_id, or_(*conditions))
        .with_for_update()
    )
    changed = 0
    for review in reviews:
        facts = _strip(review.facts, set(wanted))
        covered = day is not None and review.period_start <= day <= review.period_end
        if facts == review.facts and not (covered and review.narrative is not None):
            continue
        review.facts = facts
        if review.narrative is not None:
            review.narrative = None
            review.narrative_status = "none"
            review.narrative_error = NARRATIVE_ERASED
            review.model = None
        changed += 1
    return changed


async def erase(session: AsyncSession, obj: Erasable) -> dict[str, int]:
    """Deletes `obj` (cascades and triggers follow), then what quotes it. Returns the
    non-zero counts of what went with it (for the audit: counters only)."""
    user_id, space_id = obj.user_id, obj.space_id
    ids = [obj.id]
    texts: list[str] = []
    day: date | None = None
    if isinstance(obj, Conversation):
        # Replies to the messages hold their ids; the messages go by cascade.
        ids += list(
            await session.scalars(select(Message.id).where(Message.conversation_id == obj.id))
        )
    elif isinstance(obj, Fact):
        texts = [obj.statement]
    elif isinstance(obj, Task | Goal):
        texts = [obj.title]
    elif isinstance(obj, JournalEntry):
        day = obj.entry_date
    await session.delete(obj)
    await session.flush()

    counts = {"idempotency": await scrub_idempotency(session, user_id, ids)}
    if isinstance(obj, JournalEntry):
        counts["approvals"] = await forget_approvals(session, user_id, "journal_entry", [obj.id])
    elif isinstance(obj, Conversation):
        counts["approvals"] = await forget_approvals(session, user_id, "conversation", [obj.id])
    elif isinstance(obj, Task):
        counts["notifications"] = await forget_notifications(session, user_id, "task", [obj.id])
    elif isinstance(obj, Review):
        counts["notifications"] = await forget_notifications(session, user_id, "review", [obj.id])
    if texts or day is not None:
        counts["reviews"] = await scrub_reviews(session, user_id, space_id, texts=texts, day=day)
    return {key: value for key, value in counts.items() if value}


# --- Facts and derived objects ------------------------------------------------------------


async def fact_chain(session: AsyncSession, fact: Fact) -> list[Fact]:
    """Every version of a fact (earlier versions and corrections), oldest first."""
    found = {fact.id: fact}
    frontier = [fact]
    while frontier and len(found) < MAX_VERSIONS:
        current = frontier.pop()
        linked = await session.scalars(
            select(Fact).where(
                Fact.user_id == fact.user_id,
                or_(Fact.id == current.supersedes_id, Fact.supersedes_id == current.id),
            )
        )
        for version in linked:
            if version.id not in found:
                found[version.id] = version
                frontier.append(version)
    return sorted(found.values(), key=lambda f: (f.created_at, f.id))


@dataclass
class Derived:
    """What was saved from a journal entry or a conversation."""

    # The latest version of each fact (deleting it deletes its whole history).
    facts: list[Fact] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)


async def derived_from(
    session: AsyncSession, user_id: uuid.UUID, source_type: str, source_id: uuid.UUID
) -> Derived:
    derived = Derived()
    heads: dict[uuid.UUID, Fact] = {}
    for fact in list(
        await session.scalars(
            select(Fact).where(
                Fact.user_id == user_id,
                Fact.source_type == source_type,
                Fact.source_id == source_id,
            )
        )
    ):
        head = (await fact_chain(session, fact))[-1]
        heads[head.id] = head
    derived.facts = sorted(heads.values(), key=lambda f: (f.created_at, f.id))
    derived.tasks = list(
        await session.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.source_type == source_type,
                Task.source_id == source_id,
            )
            .order_by(Task.created_at, Task.id)
        )
    )
    if source_type == "conversation":
        messages = select(Message.id).where(Message.conversation_id == source_id)
        derived.memories = list(
            await session.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.source_type == "message",
                    Memory.source_id.in_(messages),
                )
                .order_by(Memory.created_at, Memory.id)
            )
        )
    return derived


# --- Replay after a restore ---------------------------------------------------------------

# Audited actions replayed after a restore: action -> (audited target type, model).
# Retracting a fact is replayed too: a retracted fact never reaches the assistant.
REPLAYED: dict[str, tuple[str, Any]] = {
    "fact.delete": ("fact", Fact),
    "fact.retract": ("fact", Fact),
    "memory.delete": ("memory", Memory),
    "journal.delete": ("journal_entry", JournalEntry),
    "task.delete": ("task", Task),
    "conversation.delete": ("conversation", Conversation),
    "message.delete": ("message", Message),
    "document.delete": ("document", Document),
    "dictation.delete": ("document", Document),
    "goal.delete": ("goal", Goal),
    "goal.milestone.delete": ("goal_milestone", GoalMilestone),
    "review.delete": ("review", Review),
    "finance.transaction.delete": ("transaction", Transaction),
    "finance.category.delete": ("finance_category", FinanceCategory),
}
REPLAY_ACTOR = "replay_erasures"
# Columns of the list exported by restore.sh from the replaced database's audit trail.
EXPORT_COLUMNS = ("occurred_at", "user_id", "action", "target_type", "target_id")


@dataclass(frozen=True)
class Erasure:
    """One audited erasure made after the backup (identifiers only)."""

    user_id: uuid.UUID
    action: str
    target_id: uuid.UUID


def parse_erasures(lines: Iterable[str]) -> list[Erasure]:
    """The replayable lines of an export (CSV with a header, other actions ignored)."""
    erasures = []
    for row in csv.DictReader(lines):
        known = REPLAYED.get(row.get("action") or "")
        if known is None or row.get("target_type") != known[0]:
            continue
        try:
            erasures.append(
                Erasure(
                    user_id=uuid.UUID(row.get("user_id") or ""),
                    action=row["action"],
                    target_id=uuid.UUID(row.get("target_id") or ""),
                )
            )
        except ValueError:
            continue
    return erasures


@dataclass
class ReplayResult:
    # Per action: objects of the restored database erased (or to erase, in a dry run).
    erased: Counter[str] = field(default_factory=Counter)
    # Already absent from the restored database (created after the backup, or erased).
    absent: int = 0
    # Not the user named by the audit event: never touched.
    refused: int = 0
    # Encrypted originals to remove once the transaction is committed.
    files: list[str] = field(default_factory=list)


async def _owner_of(session: AsyncSession, obj: Any) -> uuid.UUID | None:
    if isinstance(obj, GoalMilestone):
        return await session.scalar(select(Goal.user_id).where(Goal.id == obj.goal_id))
    owner: uuid.UUID = obj.user_id
    return owner


async def replay(
    session: AsyncSession, erasures: Sequence[Erasure], *, apply: bool
) -> ReplayResult:
    """Erases again, in the restored database, what the owner erased after the backup.

    Each erasure is audited again (same action, actor `system:replay_erasures`): a later
    restore of an older backup finds it in this database's audit trail too.
    """
    result = ReplayResult()
    for item in erasures:
        target_type, model = REPLAYED[item.action]
        obj = await session.get(model, item.target_id)
        if obj is None:
            result.absent += 1
            if model is Document:
                # Its original may still be in the restored files (taken before the row).
                result.files.append(DocumentStore.key_for(item.user_id, item.target_id))
            continue
        if await _owner_of(session, obj) != item.user_id:
            result.refused += 1
            continue
        if item.action == "fact.retract" and obj.status != "active":
            continue
        result.erased[item.action] += 1
        if not apply:
            continue
        space_id = getattr(obj, "space_id", None)
        if item.action == "fact.retract":
            obj.status = "retracted"
        elif isinstance(obj, GoalMilestone):
            await session.delete(obj)
        else:
            if isinstance(obj, Document) and obj.storage_key is not None:
                result.files.append(obj.storage_key)
            await erase(session, obj)
        await audit.record(
            session,
            action=item.action,
            actor_type="system",
            actor_id=REPLAY_ACTOR,
            user_id=item.user_id,
            space_id=space_id,
            target_type=target_type,
            target_id=item.target_id,
            details={"replayed": True},
        )
    await session.flush()
    return result


def summary(counts: Counter[str]) -> str:
    return ", ".join(f"{action} {count}" for action, count in sorted(counts.items())) or "none"
