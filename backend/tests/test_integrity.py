"""Data checks after a restore: `lifeos-admin verify-data` and `reconcile-originals`."""

import asyncio
import base64
import hashlib
import io
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos import cli, integrity
from lifeos.cli import main as cli_main
from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.finance.models import Transaction
from lifeos.modules.identity.models import PasskeyCredential, Space
from lifeos.modules.tasks.models import Task

from .conftest import BACKEND_DIR, Owner

ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
SECRET_TITLE = "Ordonnance du docteur Martin"


@pytest.fixture
def doc_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
        }
    )


async def _space(db: AsyncSession, owner: Owner) -> uuid.UUID:
    space = await db.scalar(select(Space).where(Space.user_id == owner.user.id))
    assert space is not None
    return space.id


async def _seed(
    database: Database, owner: Owner, store: DocumentStore, *, documents: int = 2
) -> list[Document]:
    """A task, a transaction and documents whose originals are stored (encrypted)."""
    async with database.sessionmaker() as db, db.begin():
        space_id = await _space(db, owner)
        db.add(
            PasskeyCredential(
                user_id=owner.user.id,
                credential_id=os.urandom(16),
                public_key=b"cose",
                sign_count=0,
                transports=["internal"],
                aaguid="00000000-0000-0000-0000-000000000000",
                backup_eligible=True,
                backup_state=True,
                label="Pixel",
                created_at=utcnow(),
            )
        )
        db.add(Task(user_id=owner.user.id, space_id=space_id, title="Payer la facture EDF"))
        db.add(
            Transaction(
                user_id=owner.user.id,
                space_id=space_id,
                tx_date=date(2026, 10, 1),
                amount=Decimal("42.10"),
                currency="EUR",
                merchant="Boulangerie",
            )
        )
        created = []
        for n in range(documents):
            document_id = uuid.uuid4()
            data = f"contenu {n} {SECRET_TITLE}".encode()
            document = Document(
                id=document_id,
                user_id=owner.user.id,
                space_id=space_id,
                title=f"{SECRET_TITLE} {n}",
                kind="text",
                mime="text/plain",
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status="ready",
                storage_key=DocumentStore.key_for(owner.user.id, document_id),
            )
            db.add(document)
            assert document.storage_key is not None
            store.write(document.storage_key, document_id, data)
            created.append(document)
    return created


async def _verify(database: Database, settings: Settings, **kwargs: Any) -> integrity.Report:
    async with database.sessionmaker() as db, db.begin():
        return await integrity.verify(db, settings, alembic_ini=ALEMBIC_INI, **kwargs)


async def test_a_coherent_restore_is_ok(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    await _seed(database, owner, DocumentStore(doc_settings))
    report = await _verify(database, doc_settings)

    assert report.status == "ok" and report.exit_code == 0, report.to_text()
    assert report.schema_status == "current"
    assert report.counts["users"] == 1
    assert report.counts["spaces"] == 2
    assert report.counts["tasks"] == 1 and report.tasks_by_status == {"todo": 1}
    assert report.counts["transactions"] == 1 and report.transactions_by_currency == {"EUR": 1}
    assert report.fulltext_search is True
    assert report.counts["invalid_indexes"] == 0
    assert report.originals.referenced == 2
    assert report.originals.present == 2 and report.originals.decrypted == 2
    assert report.counts["passkeys"] == 1


async def test_an_account_without_passkey_needs_an_enrollment_link(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    report = await _verify(database, doc_settings)
    assert report.status == "warning"
    assert [w.split(":")[0] for w in report.warnings] == ["no_passkey"]


async def test_missing_originals_are_reported_not_hidden(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    store = DocumentStore(doc_settings)
    first, second = await _seed(database, owner, store)
    assert first.storage_key is not None
    (Path(doc_settings.documents_dir) / first.storage_key).unlink()
    # An unreferenced file (e.g. a document deleted during the backup): only counted.
    orphan = DocumentStore.key_for(owner.user.id, uuid.uuid4())
    store.write(orphan, uuid.uuid4(), b"orphan")

    report = await _verify(database, doc_settings)
    assert report.status == "warning" and report.exit_code == 2
    assert report.originals.missing == [first.id]
    assert report.originals.present == 1 and report.originals.decrypted == 1
    assert report.originals.orphans == 1
    assert f"missing original: document {first.id}" in report.to_text()

    # Created after the files were archived: in the next backup, reported apart.
    report = await _verify(database, doc_settings, files_as_of=utcnow() - timedelta(hours=1))
    assert report.originals.missing == [] and report.originals.recent == [first.id]
    assert report.status == "ok"
    assert f"next backup): document {first.id}" in report.to_text()
    assert second.id not in report.originals.recent


async def test_wrong_key_swapped_or_stale_files_fail(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    first, second = await _seed(database, owner, DocumentStore(doc_settings))
    assert first.storage_key is not None and second.storage_key is not None

    other_key = doc_settings.model_copy(
        update={"documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode())}
    )
    report = await _verify(database, other_key)
    assert report.status == "failed" and report.exit_code == 1
    assert sorted(report.originals.unreadable) == sorted([first.id, second.id])
    assert any(p.startswith("originals_unreadable") for p in report.problems)

    # The file of another document (bound to its id): does not decrypt.
    root = Path(doc_settings.documents_dir)
    (root / first.storage_key).write_bytes((root / second.storage_key).read_bytes())
    # The right document, but not the content the database recorded (another moment).
    DocumentStore(doc_settings).write(second.storage_key, second.id, b"another version")
    report = await _verify(database, doc_settings)
    assert report.originals.unreadable == [first.id]
    assert report.originals.mismatched == [second.id]
    assert report.status == "failed"


async def test_decryption_can_be_sampled_or_skipped(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    await _seed(database, owner, DocumentStore(doc_settings), documents=3)
    report = await _verify(database, doc_settings, max_decrypt=1)
    assert report.originals.present == 3 and report.originals.decrypted == 1
    report = await _verify(database, doc_settings, max_decrypt=0)
    assert report.originals.decrypted == 0 and report.status == "ok"


async def test_archive_listing_checks_presence_only(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    first, second = await _seed(database, owner, DocumentStore(doc_settings))
    listing = ["./", f"./{owner.user.id}/", f"./{second.storage_key}", "./tmpfile.tmp"]
    report = await _verify(database, doc_settings, archived=integrity.archived_files(listing))
    assert report.originals.missing == [first.id]
    assert report.originals.present == 1 and report.originals.decrypted == 0


async def test_no_key_with_originals_is_a_failure(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    await _seed(database, owner, DocumentStore(doc_settings))
    report = await _verify(database, doc_settings.model_copy(update={"documents_key": None}))
    assert "documents_key_missing" in " ".join(report.problems)
    assert report.exit_code == 1


async def test_reports_hold_no_content(
    database: Database, owner: Owner, doc_settings: Settings
) -> None:
    first, _ = await _seed(database, owner, DocumentStore(doc_settings))
    assert first.storage_key is not None
    (Path(doc_settings.documents_dir) / first.storage_key).unlink()
    report = await _verify(database, doc_settings)
    for output in (report.to_text(), integrity.report_json(report)):
        assert "Ordonnance" not in output
        assert "Boulangerie" not in output and "42.10" not in output and "EDF" not in output
        assert owner.user.email.lower() not in output.lower()


def test_schema_status_against_the_migrations() -> None:
    assert integrity.schema_status(None, ALEMBIC_INI) == "missing"
    assert integrity.schema_status("0001", ALEMBIC_INI) == "behind"
    assert integrity.schema_status("ffff", ALEMBIC_INI) == "unknown"
    assert integrity.schema_status("0001", None) == "unchecked"


def test_instants_from_scripts() -> None:
    expected = datetime(2026, 10, 19, 7, 11, 43, 594829, tzinfo=UTC)
    assert integrity.parse_instant("2026-10-19T07:11:43.59482956Z") == expected
    assert integrity.parse_instant(str(expected.timestamp())) == expected
    assert integrity.parse_instant("2026-10-19T09:11:43+02:00").hour == 7


async def _cli(*args: str) -> int:
    # The CLI owns its event loop (asyncio.run): run it in a thread.
    return await asyncio.to_thread(cli_main, list(args))


async def test_cli_exit_codes_and_reconciliation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    database: Database,
    owner: Owner,
    doc_settings: Settings,
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: doc_settings)
    first, second = await _seed(database, owner, DocumentStore(doc_settings))
    root = Path(doc_settings.documents_dir)
    ini = str(ALEMBIC_INI)

    assert await _cli("verify-data", "--alembic-ini", ini) == 0
    assert "result: OK" in capsys.readouterr().out

    assert first.storage_key is not None
    (root / first.storage_key).unlink()
    assert await _cli("verify-data", "--alembic-ini", ini, "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "warning"
    assert result["originals"]["missing_ids"] == [str(first.id)]

    # Presence against an archive listing (standard input), as backup.sh does.
    monkeypatch.setattr("sys.stdin", io.StringIO(f"./{second.storage_key}\n"))
    assert await _cli("verify-data", "--files-from", "-", "--json") == 2
    assert json.loads(capsys.readouterr().out)["originals"]["present"] == 1

    # An empty directory (volume not mounted) is never taken for a loss.
    assert await _cli("reconcile-originals", "--documents-dir", str(root / "nowhere")) == 1
    capsys.readouterr()
    # Listed first; recorded as lost only with --yes.
    assert await _cli("reconcile-originals") == 2
    assert f"missing original: document {first.id}" in capsys.readouterr().out
    assert await _cli("reconcile-originals", "--yes") == 0
    capsys.readouterr()

    async with database.sessionmaker() as db:
        reconciled = await db.get(Document, first.id)
        kept = await db.get(Document, second.id)
        assert reconciled is not None and reconciled.storage_key is None
        assert kept is not None and kept.storage_key is not None
        event = await db.scalar(
            select(AuditEvent).where(AuditEvent.action == "document.original_missing")
        )
        assert event is not None and event.target_id == str(first.id)
        assert event.details == {}

    assert await _cli("verify-data", "--alembic-ini", ini) == 0
    assert await _cli("reconcile-originals") == 0
    assert "No missing original." in capsys.readouterr().out


async def test_cli_reports_an_unreachable_database(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    unreachable = settings.model_copy(update={"database_port": 1})
    monkeypatch.setattr(cli, "get_settings", lambda: unreachable)
    assert await _cli("verify-data") == 1
    assert "database unreachable" in capsys.readouterr().err
