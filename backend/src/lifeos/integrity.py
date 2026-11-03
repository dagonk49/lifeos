"""Data checks for restores and backups (`lifeos-admin verify-data`, `reconcile-originals`).

Answers one question: is this database, with these document originals and this key, a
usable LifeOS? Used after a restore (docs/restauration.md), by the weekly restore test on
a scratch copy, and at backup time against the list of files actually archived.

Everything reported is a count, a status or an identifier: never a title, a text, an
amount or an e-mail address (the output ends up in logs and alerts).
"""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore, OriginalMissingError, StorageError
from lifeos.modules.finance.models import FinanceCategory, Transaction
from lifeos.modules.identity.models import PasskeyCredential, Space, User
from lifeos.modules.retrieval.indexer import coverage
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.tasks.models import Task

SchemaStatus = Literal["current", "behind", "unknown", "missing", "unchecked"]
Status = Literal["ok", "warning", "failed"]

# An encrypted original on the volume: `<user uuid>/<document uuid>.bin` (DocumentStore).
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
STORED_FILE = re.compile(rf"{_UUID}/{_UUID}\.bin")
# Accented words and a plural: the French configuration (unaccent + stemming) must match.
_SEARCH_PROBE = (
    "SELECT to_tsvector('lifeos_fr', 'Électricité des bureaux') "
    "@@ websearch_to_tsquery('lifeos_fr', 'electricite bureau')"
)


@dataclass
class Originals:
    """Encrypted originals referenced by the database, compared with the files."""

    referenced: int = 0
    present: int = 0
    decrypted: int = 0
    # Referenced but absent: lost, or deleted from the volume (a real problem).
    missing: list[uuid.UUID] = field(default_factory=list)
    # Absent, but the document was created after the files were archived: the next backup
    # holds it. Reported, not counted as missing.
    recent: list[uuid.UUID] = field(default_factory=list)
    # Present but not decryptable with this key (wrong key, damaged or swapped file).
    unreadable: list[uuid.UUID] = field(default_factory=list)
    # Decrypted, but not the content the database recorded (file from another moment).
    mismatched: list[uuid.UUID] = field(default_factory=list)
    # Files that no document references (removed by the daily sweep).
    orphans: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "referenced": self.referenced,
            "present": self.present,
            "decrypted": self.decrypted,
            "missing": len(self.missing),
            "recent": len(self.recent),
            "unreadable": len(self.unreadable),
            "mismatched": len(self.mismatched),
            "orphans": self.orphans,
            "missing_ids": [str(i) for i in self.missing],
            "recent_ids": [str(i) for i in self.recent],
            "unreadable_ids": [str(i) for i in self.unreadable],
            "mismatched_ids": [str(i) for i in self.mismatched],
        }


@dataclass
class Report:
    schema_revision: str | None = None
    schema_status: SchemaStatus = "unchecked"
    counts: dict[str, int] = field(default_factory=dict)
    tasks_by_status: dict[str, int] = field(default_factory=dict)
    transactions_by_currency: dict[str, int] = field(default_factory=dict)
    fulltext_search: bool = False
    embedding_model: str = ""
    originals: Originals = field(default_factory=Originals)
    # Codes of what makes the data unusable (exit status 1) and of what needs attention.
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.problems:
            return "failed"
        return "warning" if self.warnings else "ok"

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "failed": 1, "warning": 2}[self.status]

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "schema": {"revision": self.schema_revision, "status": self.schema_status},
            "counts": self.counts,
            "tasks_by_status": self.tasks_by_status,
            "transactions_by_currency": self.transactions_by_currency,
            "search": {"fulltext": self.fulltext_search, "embedding_model": self.embedding_model},
            "originals": self.originals.to_json(),
            "problems": self.problems,
            "warnings": self.warnings,
        }

    def to_text(self) -> str:
        c = self.counts
        o = self.originals

        def by(values: dict[str, int]) -> str:
            return ", ".join(f"{k} {v}" for k, v in sorted(values.items())) or "none"

        lines = [
            "LifeOS data check (counts and identifiers only, no content)",
            f"schema revision: {self.schema_revision or 'none'} ({self.schema_status})",
        ]
        if not c:  # stopped at the schema check
            lines += [f"problem: {p}" for p in self.problems]
            lines.append(f"result: {self.status.upper()}")
            return "\n".join(lines)
        lines += [
            f"owner: {c['users']} user(s), {c['passkeys']} active passkey(s), "
            f"{c['spaces']} space(s)",
            f"tasks: {c['tasks']} ({by(self.tasks_by_status)})",
            f"finances: {c['transactions']} transaction(s) "
            f"({by(self.transactions_by_currency)}), {c['categories']} categorie(s)",
            f"documents: {c['documents']}, {o.referenced} encrypted original(s) referenced",
            "search: full-text "
            + ("OK" if self.fulltext_search else "BROKEN")
            + f", semantic index {c['indexed']}/{c['indexable']} for {self.embedding_model}"
            + f" ({c['embeddings_other_models']} vector(s) of other models)"
            + f", {c['invalid_indexes']} invalid index(es)",
            f"originals: {o.present} present, {o.decrypted} decrypted, "
            f"{len(o.missing)} missing, {len(o.unreadable)} unreadable, "
            f"{len(o.mismatched)} mismatched, {len(o.recent)} newer than the files, "
            f"{o.orphans} orphan file(s)",
        ]
        lines += [f"missing original: document {i}" for i in o.missing]
        lines += [f"unreadable original: document {i}" for i in o.unreadable]
        lines += [f"mismatched original: document {i}" for i in o.mismatched]
        lines += [f"original newer than the files (next backup): document {i}" for i in o.recent]
        lines += [f"problem: {p}" for p in self.problems]
        lines += [f"warning: {w}" for w in self.warnings]
        lines.append(f"result: {self.status.upper()}")
        return "\n".join(lines)


# --- Schema -------------------------------------------------------------------------------


def schema_status(revision: str | None, alembic_ini: Path | None) -> SchemaStatus:
    """Where the database stands against the migrations of this code."""
    if revision is None:
        return "missing"
    if alembic_ini is None or not alembic_ini.is_file():
        return "unchecked"
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError

    script = ScriptDirectory.from_config(Config(str(alembic_ini)))
    if revision in script.get_heads():
        return "current"
    try:
        known = script.get_revision(revision) is not None
    except CommandError:
        known = False
    return "behind" if known else "unknown"


async def _revision(session: AsyncSession) -> str | None:
    try:
        async with session.begin_nested():
            value = await session.scalar(text("SELECT version_num FROM alembic_version"))
    except DBAPIError:
        return None
    return str(value) if value is not None else None


# --- Counts -------------------------------------------------------------------------------


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return int(await session.scalar(stmt) or 0)


async def _grouped(session: AsyncSession, column: Any) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column))
    return {str(key): int(n) for key, n in rows.all()}


async def _fulltext_ok(session: AsyncSession) -> bool:
    try:
        async with session.begin_nested():
            return bool(await session.scalar(text(_SEARCH_PROBE)))
    except DBAPIError:
        return False


# --- Originals ----------------------------------------------------------------------------


def stored_files(root: Path) -> set[str]:
    """Storage keys of the encrypted originals found under a documents directory."""
    if not root.is_dir():
        return set()
    keys = set()
    for path in root.glob("*/*.bin"):
        key = path.relative_to(root).as_posix()
        if STORED_FILE.fullmatch(key) and path.is_file():
            keys.add(key)
    return keys


def archived_files(lines: Iterable[str]) -> set[str]:
    """Storage keys from a listing of the documents archive (`tar -t`, `./<user>/<doc>.bin`)."""
    keys = set()
    for line in lines:
        key = line.strip().removeprefix("./")
        if STORED_FILE.fullmatch(key):
            keys.add(key)
    return keys


def _decrypts(store: DocumentStore, key: str, document_id: uuid.UUID, sha256: str) -> bool | None:
    """True when the original decrypts to the recorded content, False when it decrypts to
    something else, None when it does not decrypt at all."""
    try:
        data = store.read(key, document_id)
    except OriginalMissingError:
        raise
    except StorageError:
        return None
    return hashlib.sha256(data).hexdigest() == sha256


async def _check_originals(
    session: AsyncSession,
    report: Report,
    *,
    store: DocumentStore | None,
    files: set[str],
    files_as_of: datetime | None,
    max_decrypt: int | None,
) -> None:
    originals = report.originals
    rows = (
        await session.execute(
            select(Document.id, Document.storage_key, Document.sha256, Document.created_at)
            .where(Document.storage_key.is_not(None))
            .order_by(Document.created_at.desc(), Document.id)
        )
    ).all()
    originals.referenced = len(rows)
    referenced = {str(row.storage_key) for row in rows}
    originals.orphans = len(files - referenced)
    budget = max_decrypt
    for document_id, storage_key, sha256, created_at in rows:
        key = str(storage_key)
        if key not in files:
            if files_as_of is not None and created_at > files_as_of:
                originals.recent.append(document_id)
            else:
                originals.missing.append(document_id)
            continue
        originals.present += 1
        if store is None or (budget is not None and budget <= 0):
            continue
        if budget is not None:
            budget -= 1
        try:
            matches = await asyncio.to_thread(_decrypts, store, key, document_id, sha256)
        except OriginalMissingError:
            # Removed between the listing and the read (a deletion or a purge right now).
            originals.present -= 1
            originals.missing.append(document_id)
            continue
        if matches is None:
            originals.unreadable.append(document_id)
        else:
            originals.decrypted += 1
            if not matches:
                originals.mismatched.append(document_id)


# --- Entry point --------------------------------------------------------------------------


async def verify(
    session: AsyncSession,
    settings: Settings,
    *,
    documents_dir: Path | None = None,
    archived: set[str] | None = None,
    files_as_of: datetime | None = None,
    max_decrypt: int | None = None,
    alembic_ini: Path | None = None,
    require_head: bool = False,
) -> Report:
    """Checks a LifeOS database and its originals. `archived` (storage keys listed from a
    documents archive) replaces the documents directory: presence only, no decryption."""
    report = Report()
    report.schema_revision = await _revision(session)
    report.schema_status = schema_status(report.schema_revision, alembic_ini)
    if report.schema_status == "missing":
        report.problems.append("schema_missing: not a LifeOS database (no alembic_version)")
        return report
    if report.schema_status == "unknown":
        report.problems.append(
            "schema_unknown: made by a newer LifeOS; check out the version of the backup"
        )
        return report
    if report.schema_status == "behind" and require_head:
        # Otherwise expected: a backup made before an update, restored by newer code (the
        # migrations bring it up to date).
        report.problems.append("schema_behind: migrations pending (docker compose run migrate)")

    c = report.counts
    c["users"] = await _count(session, User)
    c["passkeys"] = await _count(session, PasskeyCredential, PasskeyCredential.revoked_at.is_(None))
    c["spaces"] = await _count(session, Space)
    c["tasks"] = await _count(session, Task)
    report.tasks_by_status = await _grouped(session, Task.status)
    c["transactions"] = await _count(session, Transaction)
    report.transactions_by_currency = await _grouped(session, Transaction.currency)
    c["categories"] = await _count(session, FinanceCategory)
    c["documents"] = await _count(session, Document)
    if c["users"] == 0:
        report.problems.append("no_owner: no user account in this database")
    elif c["passkeys"] == 0:
        report.warnings.append("no_passkey: issue a link with `lifeos-admin enrollment-token`")

    # Search: the French configuration (unaccent, stemming), valid indexes, and how much of
    # the semantic index matches the configured model (otherwise it is rebuilt).
    report.fulltext_search = await _fulltext_ok(session)
    if not report.fulltext_search:
        report.problems.append("fulltext_broken: text search configuration lifeos_fr unusable")
    c["invalid_indexes"] = int(
        await session.scalar(
            text(
                "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND NOT (i.indisvalid AND i.indisready)"
            )
        )
        or 0
    )
    if c["invalid_indexes"]:
        report.problems.append("invalid_indexes: rebuild them (REINDEX DATABASE)")
    report.embedding_model = settings.embeddings_model
    c["indexed"] = c["indexable"] = 0
    for user_id in await session.scalars(select(User.id)):
        progress = await coverage(session, user_id, settings.embeddings_model)
        c["indexed"] += int(progress["indexed"])
        c["indexable"] += int(progress["total"])
    c["embeddings_other_models"] = await _count(
        session, Embedding, Embedding.model != settings.embeddings_model
    )

    # Originals: against the archive listing, or against the files (and the key).
    store: DocumentStore | None = None
    if archived is not None:
        files = archived
    else:
        root = documents_dir or Path(settings.documents_dir)
        local = settings.model_copy(update={"documents_dir": str(root)})
        store = DocumentStore(local)
        files = await asyncio.to_thread(stored_files, root)
        if not store.available:
            store = None
            if files:
                report.problems.append("documents_key_missing: originals cannot be decrypted")
    await _check_originals(
        session,
        report,
        store=store,
        files=files,
        files_as_of=files_as_of,
        max_decrypt=max_decrypt,
    )
    o = report.originals
    if o.unreadable:
        report.problems.append("originals_unreadable: wrong lifeos_documents_key, or damaged files")
    if o.mismatched:
        report.problems.append("originals_mismatched: files from another moment than the database")
    if o.missing:
        report.warnings.append(
            "originals_missing: see docs/restauration.md (an older backup may hold them; "
            "otherwise `lifeos-admin reconcile-originals --yes`)"
        )
    return report


def report_json(report: Report) -> str:
    return json.dumps(report.to_json(), sort_keys=True)


def parse_instant(value: str) -> datetime:
    """A Unix time in seconds, or an ISO 8601 date (restic prints nanoseconds)."""
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except ValueError:
        pass
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    instant = datetime.fromisoformat(trimmed)
    return instant.astimezone(UTC) if instant.tzinfo else instant.replace(tzinfo=UTC)


# --- Reconciliation -----------------------------------------------------------------------


async def missing_originals(session: AsyncSession, root: Path) -> list[Document]:
    """Documents whose encrypted original is referenced but absent from the volume."""
    files = await asyncio.to_thread(stored_files, root)
    documents = await session.scalars(
        select(Document)
        .where(Document.storage_key.is_not(None))
        .order_by(Document.created_at)
        .with_for_update(skip_locked=True)
    )
    return [d for d in documents if d.storage_key not in files]


async def reconcile_originals(session: AsyncSession, documents: list[Document]) -> int:
    """Records that these originals are lost: the documents (metadata, extracted text)
    stay and say « original no longer kept » instead of failing on download. One audit
    event per document (identifiers only), so that the loss stays visible."""
    for document in documents:
        document.storage_key = None
        document.original_until = None
        await audit.record(
            session,
            action="document.original_missing",
            actor_type="system",
            actor_id="admin",
            user_id=document.user_id,
            space_id=document.space_id,
            target_type="document",
            target_id=document.id,
        )
    await session.flush()
    return len(documents)
