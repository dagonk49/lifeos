"""Reversibility (task 33): the export is complete, readable by other tools, limited to
its owner, and never carries a secret."""

import base64
import csv
import io
import json
import os
import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import LargeBinary, Table, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.base import Base
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.finance.models import CategoryRule, FinanceCategory, Transaction
from lifeos.modules.goals.models import Goal, GoalCheckin, GoalMilestone
from lifeos.modules.guardian.models import AgentPermission, Approval, LLMCall
from lifeos.modules.identity.models import PasskeyCredential, Space, User, UserSession
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.mail.models import MailMessage
from lifeos.modules.memory.models import (
    Fact,
    FactPairDismissal,
    Memory,
    MemoryRevision,
    memory_facts,
)
from lifeos.modules.notifications.models import Notification, NotificationPreference
from lifeos.modules.privacy import ics
from lifeos.modules.privacy.archive import neutral, safe_name
from lifeos.modules.privacy.export import (
    EXPORT_EXCLUDED,
    EXPORTED,
    OMITTED_COLUMNS,
    omitted,
    owner_filter,
)
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks.models import Task, TaskDependency

from .conftest import Owner, login

NOW = datetime(2026, 10, 20, 8, 30, tzinfo=UTC)
NOTES = 'Virgule, point-virgule; "guillemets"\nseconde ligne — été'
MERCHANT = '=HYPERLINK("http://example.org")'
# Values that must never leave the server.
SECRETS = ("SEALED-REFRESH-TOKEN", "SEALED-ACCESS-TOKEN", "PAGE-TOKEN-CURSOR")


@pytest.fixture
def doc_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
        }
    )


@pytest.fixture
async def app(doc_settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    """The application with document storage configured (key and directory)."""
    application = create_app(doc_settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def store(doc_settings: Settings) -> DocumentStore:
    return DocumentStore(doc_settings)


# --- Seeding: one row (at least) in every exported table ----------------------------------


async def seed(db: AsyncSession, user_id: uuid.UUID, space_id: uuid.UUID, mark: str) -> None:
    """Rows of `user_id` in `space_id`, each text tagged with `mark`."""

    def add(obj: Any) -> Any:
        db.add(obj)
        return obj

    owned = {"user_id": user_id, "space_id": space_id}
    first = add(
        Task(
            **owned,
            title=f"{mark} Acheter du pain",
            notes=NOTES,
            due_at=NOW + timedelta(days=1),
            remind_at=NOW + timedelta(hours=20),
            tags=["maison", "courses"],
            priority="high",
        )
    )
    second = add(Task(**owned, title=f"{mark} Faire les sandwichs", status="done"))
    second.completed_at = NOW
    facts = [
        add(
            Fact(
                **owned,
                kind="preference",
                statement=f"{mark} préfère le thé {n}",
                origin="user_declared",
                confidence=Decimal("0.90"),
                source_type="manual",
            )
        )
        for n in (1, 2)
    ]
    memory = add(Memory(**owned, title=f"{mark} souvenir", content="Plage", origin="user_declared"))
    conversation = add(Conversation(**owned, title=f"{mark} conversation"))
    category = add(FinanceCategory(**owned, name=f"{mark} Courses", monthly_budget=Decimal("300")))
    goal = add(
        Goal(
            **owned,
            title=f"{mark} Courir",
            kind="measurable",
            start_value=Decimal("0.00"),
            target_value=Decimal("10.50"),
            due_date=date(2026, 12, 31),
        )
    )
    account = add(
        ConnectorAccount(
            user_id=user_id,
            provider="fake",
            subject=f"{mark}-subject",
            email=f"{mark.lower()}@example.org",
            scopes=["calendar.read"],
            refresh_token_enc=SECRETS[0].encode(),
            access_token_enc=SECRETS[1].encode(),
            connected_at=NOW,
        )
    )
    await db.flush()
    source = add(
        ConnectorSource(
            **owned,
            account_id=account.id,
            kind="calendar",
            provider_ref="primary",
            name=f"{mark} agenda",
            page_token=SECRETS[2],
        )
    )
    document = add(
        Document(
            **owned,
            title=f"{mark} note",
            kind="text",
            mime="text/plain",
            size_bytes=5,
            sha256="0" * 64,
            status="ready",
            extracted_text=f"{mark} texte extrait",
            text_confidence=Decimal("0.950"),
        )
    )
    await db.flush()
    add(TaskDependency(**owned, task_id=second.id, depends_on_id=first.id, created_at=NOW))
    a, b = sorted(f.id for f in facts)
    add(FactPairDismissal(**owned, fact_a=a, fact_b=b, created_at=NOW))
    add(JournalEntry(**owned, entry_date=date(2026, 10, 19), content=f"{mark} journal\n{NOTES}"))
    add(
        MemoryRevision(
            memory_id=memory.id,
            revision=1,
            title=f"{mark} souvenir",
            content="Plage",
            importance=3,
            protected=False,
            changed_at=NOW,
        )
    )
    await db.execute(insert(memory_facts).values(memory_id=memory.id, fact_id=facts[0].id))
    add(
        Message(
            **owned,
            conversation_id=conversation.id,
            role="user",
            content=f"{mark} bonjour",
            created_at=NOW,
        )
    )
    add(CategoryRule(**owned, merchant_key=f"{mark.lower()} boulangerie", category_id=category.id))
    add(
        Transaction(
            **owned,
            tx_date=date(2026, 10, 18),
            amount=Decimal("12.50"),
            merchant=MERCHANT if mark == "PROPRIO" else f"{mark} marchand",
            label=f"{mark} pain",
            notes=NOTES,
            category_id=category.id,
            document_id=document.id,
        )
    )
    add(Transaction(**owned, tx_date=date(2026, 10, 1), amount=Decimal("1500"), direction="income"))
    add(GoalMilestone(goal_id=goal.id, title=f"{mark} 5 km", due_date=date(2026, 11, 15)))
    add(GoalCheckin(goal_id=goal.id, value=Decimal("2.50"), note=f"{mark} bien", created_at=NOW))
    add(
        Review(
            **owned,
            period="weekly",
            period_start=date(2026, 10, 12),
            period_end=date(2026, 10, 18),
            facts={"tasks_done": 1, "titles": [f"{mark} Faire les sandwichs"]},
            generated_by="user",
            generated_at=NOW,
        )
    )
    add(
        Notification(
            **owned,
            category="important",
            title=f"{mark} Rappel",
            dedup_key=f"{mark}-rappel",
            deliver_after=NOW,
            created_at=NOW,
        )
    )
    add(
        NotificationPreference(
            user_id=user_id,
            category="suggestion",
            max_per_day=2,
            digest_times=["18:00"],
            updated_at=NOW,
        )
    )
    add(
        Approval(
            **owned,
            action="task.delete",
            params={"task_id": str(first.id)},
            preview={"title": f"{mark} Acheter du pain"},
            level="approval",
            requested_by="agent:planning",
            expires_at=NOW + timedelta(days=7),
            created_at=NOW,
        )
    )
    add(
        AgentPermission(
            **owned,
            agent="planning",
            resource="tasks",
            access="read",
            purpose=f"{mark} planifier",
            created_at=NOW,
        )
    )
    add(
        LLMCall(
            user_id=user_id,
            space_id=space_id,
            purpose="chat.reply",
            provider="fake",
            outcome="ok",
            cost_usd=Decimal("0.001250"),
            created_at=NOW,
        )
    )
    add(
        ConnectorGrant(
            user_id=user_id,
            account_id=account.id,
            space_id=space_id,
            capability="calendar.read",
            created_at=NOW,
        )
    )
    add(
        CalendarEvent(
            **owned,
            source_id=source.id,
            provider_event_id=f"{mark}-e1",
            summary=f"{mark} Réunion, budget; suite",
            description=NOTES,
            location="Salle 2",
            starts_at=NOW,
            ends_at=NOW + timedelta(hours=1),
        )
    )
    add(
        CalendarEvent(
            **owned,
            source_id=source.id,
            provider_event_id=f"{mark}-e2",
            summary=f"{mark} Congés",
            start_date=date(2026, 10, 23),
            end_date=date(2026, 10, 24),
            status="tentative",
            busy=False,
        )
    )
    mailbox = add(
        ConnectorSource(
            **owned,
            account_id=account.id,
            kind="mail_label",
            provider_ref="INBOX",
            name=f"{mark} boîte",
        )
    )
    await db.flush()
    add(
        MailMessage(
            **owned,
            source_id=mailbox.id,
            provider_message_id=f"{mark}-m1",
            from_address="lea@example.org",
            subject=f"{mark} Pique-nique",
            body_text=NOTES,
            received_at=NOW,
            purge_after=NOW + timedelta(days=90),
        )
    )
    add(
        AuditEvent(
            user_id=user_id, actor_type="user", actor_id=mark, action="seed", outcome="success"
        )
    )
    await db.commit()


async def stranger(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Another user, with a space, a passkey, a session and rows in every table."""
    user = User(email="inconnu@example.org", display_name="INCONNU")
    db.add(user)
    await db.flush()
    space = Space(user_id=user.id, kind="personal", name="INCONNU espace")
    db.add(space)
    db.add(
        PasskeyCredential(
            user_id=user.id,
            credential_id=b"INCONNU-credential",
            public_key=b"INCONNU-public-key",
            sign_count=0,
            transports=["internal"],
            aaguid="0" * 32,
            backup_eligible=False,
            backup_state=False,
            label="INCONNU clé",
            created_at=NOW,
        )
    )
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=b"INCONNU-token-hash",
            authenticated_at=NOW,
            last_seen_at=NOW,
            expires_at=NOW + timedelta(days=30),
            user_agent="INCONNU navigateur",
            created_at=NOW,
        )
    )
    await db.flush()
    await seed(db, user.id, space.id, "INCONNU")
    return user.id, space.id


async def archive(client: AsyncClient, query: str = "") -> zipfile.ZipFile:
    response = await client.get(f"/v1/privacy/export.zip{query}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert re.fullmatch(
        r'attachment; filename="lifeos-export-\d{8}\.zip"', response.headers["content-disposition"]
    )
    return zipfile.ZipFile(io.BytesIO(response.content))


def files(zipped: zipfile.ZipFile) -> dict[str, bytes]:
    """Contents by path, without the archive's root folder (which must hold everything)."""
    names = zipped.namelist()
    root = names[0].split("/")[0]
    assert re.fullmatch(r"lifeos-export-\d{8}", root)
    assert all(n.startswith(f"{root}/") and ".." not in n for n in names)
    return {n.removeprefix(f"{root}/"): zipped.read(n) for n in names}


# --- Completeness ---------------------------------------------------------------------------


def _owned_tables() -> set[str]:
    """Tables holding a user's rows: a `user_id`, a foreign key to `users`, or a foreign
    key to such a table (transitively)."""
    owned = {"users"}
    changed = True
    while changed:
        changed = False
        for t in Base.metadata.sorted_tables:
            if t.name in owned:
                continue
            if "user_id" in t.c or any(fk.column.table.name in owned for fk in t.foreign_keys):
                owned.add(t.name)
                changed = True
    return owned


def test_every_user_table_is_exported_or_excluded_with_a_reason() -> None:
    owned = _owned_tables()
    placed = set(EXPORTED) | set(EXPORT_EXCLUDED)
    missing = owned - placed
    assert not missing, f"tables to export or to exclude (with a reason): {sorted(missing)}"
    assert not set(EXPORTED) & set(EXPORT_EXCLUDED)
    assert placed <= set(Base.metadata.tables), "unknown tables listed"
    assert len(EXPORTED) == len(set(EXPORTED))
    assert all(len(reason) > 10 for reason in EXPORT_EXCLUDED.values())
    for name in EXPORTED:
        table: Table = Base.metadata.tables[name]
        owner_filter(table, uuid.uuid4())  # every exported table knows its owner
        unknown = set(OMITTED_COLUMNS.get(name, {})) - set(table.c.keys())
        assert not unknown, f"{name}: omitted columns that do not exist: {unknown}"


def test_no_secret_column_is_exported() -> None:
    suspicious = re.compile(r"(_enc|_hash|token|secret|verifier|public_key|credential_id)$")
    for name in EXPORTED:
        for column in Base.metadata.tables[name].c:
            if column.name in omitted(name):
                continue
            assert not isinstance(column.type, LargeBinary), f"{name}.{column.name} is binary"
            if not column.foreign_keys:  # a reference to another row is an identifier
                assert not suspicious.search(column.name), f"{name}.{column.name} looks secret"


async def test_the_export_holds_every_row_and_column_with_exact_decimals(
    client: AsyncClient, logged_in: Owner, spaces: dict[str, str], db: AsyncSession
) -> None:
    await seed(db, logged_in.user.id, uuid.UUID(spaces["personal"]), "PROPRIO")
    counts = {
        name: await db.scalar(select(func.count()).select_from(Base.metadata.tables[name]))
        for name in EXPORTED
    }
    response = await client.get("/v1/privacy/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert re.fullmatch(
        r'attachment; filename="lifeos-export-\d{8}\.json"', response.headers["content-disposition"]
    )
    data = response.json()
    assert (data["format"], data["version"]) == ("lifeos-export", 2)
    assert data["excluded"]["tables"] == EXPORT_EXCLUDED
    assert data["user"]["email"] == "owner@example.org"
    assert {"timezone", "quiet_hours_start", "llm_monthly_token_budget"} <= set(data["user"])
    for name in EXPORTED:
        if name == "users":
            continue
        rows = data[name]
        assert rows, f"{name}: seed a row so that the test covers it"
        assert len(rows) == counts[name], name
        expected = {c.name for c in Base.metadata.tables[name].c} - omitted(name)
        if name == "documents":
            expected.add("original_available")
        assert set(rows[0]) == expected, name

    [tx] = [t for t in data["transactions"] if t["direction"] == "expense"]
    assert tx["amount"] == "12.50" and Decimal(tx["amount"]) == Decimal("12.50")
    assert data["finance_categories"][0]["monthly_budget"] == "300.00"
    assert data["goals"][0]["target_value"] == "10.50"
    assert data["goal_checkins"][0]["value"] == "2.50"
    assert data["facts"][0]["confidence"] == "0.90"
    assert data["llm_calls"][0]["cost_usd"] == "0.001250"
    assert data["documents"][0]["original_available"] is False
    [link] = data["memory_facts"]
    assert link["memory_id"] == data["memories"][0]["id"]
    # The passkey and the session of the owner, without their key material.
    [passkey] = data["passkey_credentials"]
    assert passkey["label"] == "Pixel" and "public_key" not in passkey
    assert "token_hash" not in data["user_sessions"][0]
    for secret in SECRETS:
        assert secret not in response.text


# --- Owner only ----------------------------------------------------------------------------


async def test_the_export_is_limited_to_its_owner(
    client: AsyncClient, logged_in: Owner, spaces: dict[str, str], db: AsyncSession
) -> None:
    await seed(db, logged_in.user.id, uuid.UUID(spaces["personal"]), "PROPRIO")
    other_user, other_space = await stranger(db)
    other_ids = {str(other_user), str(other_space)}
    for name in EXPORTED:
        target = Base.metadata.tables[name]
        if "id" in target.c and name != "audit_events":  # (integer ids there)
            other_ids |= {
                str(i)
                for i in await db.scalars(
                    select(target.c.id).where(owner_filter(target, other_user))
                )
            }
    json_text = (await client.get("/v1/privacy/export")).text
    contents = files(await archive(client))
    texts = [json_text, *(c.decode("utf-8-sig") for c in contents.values())]
    for text in texts:
        assert "INCONNU" not in text
        assert not [i for i in other_ids if i in text]
        for secret in SECRETS:
            assert secret not in text
    assert "PROPRIO Acheter du pain" in json_text
    assert "PROPRIO Acheter du pain" in contents["tasks.csv"].decode()

    client.cookies.clear()
    for path in ("/v1/privacy/export", "/v1/privacy/export.zip"):
        refused = await client.get(path)
        assert refused.status_code == 401
        assert refused.json()["code"] == "unauthenticated"


# --- Readable by other tools ---------------------------------------------------------------


def _csv(content: bytes) -> list[dict[str, str]]:
    assert content.startswith("﻿".encode())  # spreadsheets read it as UTF-8
    text = content.decode("utf-8-sig")
    assert text.endswith("\r\n")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _ics(content: bytes) -> list[dict[str, list[str]]]:
    """Checks the framing (CRLF, 75 octets, balanced components) and returns, per
    component, its properties (name with parameters -> values), nested ones included."""
    text = content.decode()
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    raw = text.split("\r\n")[:-1]
    assert all(len(line.encode()) <= ics.MAX_OCTETS for line in raw)
    lines: list[str] = []
    for line in raw:
        if line.startswith(" "):
            lines[-1] += line[1:]
        else:
            lines.append(line)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    assert "VERSION:2.0" in lines and any(line.startswith("PRODID:") for line in lines)
    stack: list[str] = []
    components: list[dict[str, list[str]]] = []
    current: list[dict[str, list[str]]] = []
    for line in lines:
        name, _, value = line.partition(":")
        if name == "BEGIN":
            stack.append(value)
            current.append({"_type": [value]})
        elif name == "END":
            assert stack.pop() == value
            done = current.pop()
            if value in ("VEVENT", "VTODO"):
                components.append(done)
            elif current and value == "VALARM":
                current[-1].setdefault("VALARM", []).extend(
                    f"{k}={v[0]}" for k, v in done.items() if k != "_type"
                )
        else:
            current[-1].setdefault(name, []).append(value)
    assert not stack
    return components


async def test_the_archive_is_readable_by_other_tools(
    client: AsyncClient, logged_in: Owner, spaces: dict[str, str], db: AsyncSession
) -> None:
    await seed(db, logged_in.user.id, uuid.UUID(spaces["personal"]), "PROPRIO")
    await db.execute(update(User).values(timezone="Europe/Paris"))
    await db.commit()
    contents = files(await archive(client))
    assert {
        "README.md",
        "lifeos-export.json",
        "tasks.csv",
        "transactions.csv",
        "tasks.ics",
        "calendar.ics",
    } <= set(contents)
    assert not any(n.startswith("originaux/") for n in contents)
    readme = contents["README.md"].decode()
    assert "ne sont pas chiffrés" in readme and "ne sont pas inclus" in readme
    assert all(name in readme for name in EXPORT_EXCLUDED)
    data = json.loads(contents["lifeos-export.json"])
    assert data["version"] == 2

    # CSV: accents, separators, quotes and newlines survive; formulas are neutralised.
    tasks = {row["title"]: row for row in _csv(contents["tasks.csv"])}
    bread = tasks["PROPRIO Acheter du pain"]
    assert bread["notes"] == NOTES and bread["tags"] == "maison, courses"
    assert bread["due_at"] == "2026-10-21T10:30:00+02:00"  # owner's time zone, with offset
    assert tasks["PROPRIO Faire les sandwichs"]["depends_on"] == bread["id"]
    transactions = _csv(contents["transactions.csv"])
    [expense] = [t for t in transactions if t["direction"] == "expense"]
    [income] = [t for t in transactions if t["direction"] == "income"]
    assert expense["merchant"] == "'" + MERCHANT
    assert (expense["amount"], expense["signed_amount"]) == ("12.50", "-12.50")
    assert (income["amount"], income["signed_amount"]) == ("1500.00", "1500.00")
    assert expense["category"] == "PROPRIO Courses" and expense["notes"] == NOTES
    assert expense["date"] == "2026-10-18" and expense["space"]

    # iCalendar: framing, escaping, UTC instants, all-day dates, alarms, stable UIDs.
    events = {_unescape(e["SUMMARY"][0]): e for e in _ics(contents["calendar.ics"])}
    meeting = events["PROPRIO Réunion, budget; suite"]
    assert meeting["DTSTART"] == ["20261020T083000Z"] and meeting["DTEND"] == ["20261020T093000Z"]
    assert _unescape(meeting["DESCRIPTION"][0]) == NOTES
    holiday = events["PROPRIO Congés"]
    assert holiday["DTSTART;VALUE=DATE"] == ["20261023"]
    assert holiday["DTEND;VALUE=DATE"] == ["20261024"]
    assert holiday["STATUS"] == ["TENTATIVE"] and holiday["TRANSP"] == ["TRANSPARENT"]
    todos = {_unescape(t["SUMMARY"][0]): t for t in _ics(contents["tasks.ics"])}
    todo = todos["PROPRIO Acheter du pain"]
    assert todo["UID"] == [f"task-{bread['id']}@lifeos"]
    assert todo["DUE"] == ["20261021T083000Z"] and todo["PRIORITY"] == ["1"]
    assert todo["CATEGORIES"] == ["maison,courses"]
    assert "TRIGGER;VALUE=DATE-TIME=20261021T043000Z" in todo["VALARM"]
    done = todos["PROPRIO Faire les sandwichs"]
    assert done["STATUS"] == ["COMPLETED"]
    assert done["RELATED-TO;RELTYPE=DEPENDS-ON"] == [f"task-{bread['id']}@lifeos"]
    again = files(await archive(client))
    assert [t["UID"] for t in _ics(again["tasks.ics"])] == [
        t["UID"] for t in _ics(contents["tasks.ics"])
    ]

    # Markdown journal: a YAML (JSON scalars) front matter, then the text.
    [(path, entry)] = [(p, c) for p, c in contents.items() if p.startswith("journal/")]
    assert re.fullmatch(r"journal/2026-10-19-[0-9a-f]{8}\.md", path)
    _, front, body = entry.decode().split("---\n", 2)
    meta = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in front.splitlines())}
    assert meta["date"] == "2026-10-19" and meta["id"] == data["journal_entries"][0]["id"]
    assert NOTES in body


# --- Decrypted originals ------------------------------------------------------------------


async def test_decrypted_originals_need_a_recent_passkey_and_are_audited(
    client: AsyncClient,
    logged_in: Owner,
    spaces: dict[str, str],
    db: AsyncSession,
    store: DocumentStore,
) -> None:
    space = uuid.UUID(spaces["personal"])
    owned = {"user_id": logged_in.user.id, "space_id": space}
    pdf = b"%PDF-1.7 facture"

    def document(title: str, kind: str, mime: str, **fields: Any) -> Document:
        doc = Document(
            **owned,
            id=uuid.uuid4(),
            title=title,
            kind=kind,
            mime=mime,
            size_bytes=10,
            sha256="0" * 64,
            status="ready",
            **fields,
        )
        doc.storage_key = DocumentStore.key_for(logged_in.user.id, doc.id)
        db.add(doc)
        return doc

    invoice = document("Facture été/../2026", "pdf", "application/pdf")
    store.write(invoice.storage_key or "", invoice.id, pdf)
    picture = document("Photo", "image", "image/png", original_sanitized=False)
    store.write(picture.storage_key or "", picture.id, b"\x89PNG with GPS")
    lost = document("Perdu", "pdf", "application/pdf")  # no file on the volume
    await db.commit()

    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    # Every export needs a recent passkey sign-in: a stolen session is not enough.
    for path in (
        "/v1/privacy/export",
        "/v1/privacy/export.zip",
        "/v1/privacy/export.zip?originals=true",
    ):
        refused = await client.get(path)
        assert refused.status_code == 403
        assert refused.json()["code"] == "reauthentication_required"
    denied = list(
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "data.export", AuditEvent.outcome == "denied"
            )
        )
    )
    assert len(denied) == 3

    await login(client, logged_in.authenticator)
    contents = files(await archive(client, "?originals=true"))
    originals = {p: c for p, c in contents.items() if p.startswith("originaux/")}
    [(path, content)] = originals.items()
    assert content == pdf
    assert re.fullmatch(
        rf"originaux/\d{{4}}-\d\d-\d\d-{str(invoice.id)[:8]}-Facture-été-2026\.pdf", path
    )
    readme = contents["README.md"].decode()
    assert "1 original(aux) déchiffré(s)" in readme
    assert str(lost.id) in readme  # unreadable: named, never silently dropped
    assert "1 image(s) jamais nettoyée(s)" in readme

    events = list(
        await db.scalars(
            select(AuditEvent).where(AuditEvent.action == "privacy.export").order_by(AuditEvent.id)
        )
    )
    assert [e.details.get("format") for e in events] == ["zip"]
    assert events[-1].details["originals"] == 2
    assert all(set(e.details) <= {"format", "rows", "originals"} for e in events)
    assert "Facture" not in json.dumps([e.details for e in events])


# --- Formats, unit by unit ----------------------------------------------------------------


def test_ics_text_is_escaped_and_folded_without_splitting_a_character() -> None:
    assert ics.escape_text("a\\b;c,d\r\ne\rf\x00g\x7f") == "a\\\\b\\;c\\,d\\ne\\nfg"
    line = "SUMMARY:" + "é" * 50 + "😀" * 20
    folded = ics.fold(line)
    parts = folded.split("\r\n")
    assert len(parts) > 1 and all(len(p.encode()) <= 75 for p in parts)
    assert all(p.startswith(" ") for p in parts[1:])
    assert parts[0] + "".join(p[1:] for p in parts[1:]) == line
    assert ics.fold("SHORT:x") == "SHORT:x"


def test_spreadsheet_formulas_and_file_names_are_neutralised() -> None:
    for value in ("=1+1", "+33 6", "-2", "@SUM(A1)", "\tx", "\rx"):
        assert neutral(value) == "'" + value
    assert neutral("Boulangerie") == "Boulangerie" and neutral(None) == ""
    assert safe_name("../../etc/passwd") == "etc-passwd"
    assert safe_name("Relevé : mai/juin") == "Relevé-mai-juin"
    assert safe_name("...") == "document"
